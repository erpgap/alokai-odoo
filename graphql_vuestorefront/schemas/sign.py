# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphql import GraphQLError
import odoo
import uuid
import re
from odoo import http, _
from odoo.http import request
from odoo.exceptions import UserError, AccessDenied
from odoo.addons.auth_signup.models.res_users import SignupError
from odoo.addons.graphql_vuestorefront.schemas.objects import User, Order, WishlistItem
from odoo.addons.website_mass_mailing.controllers.main import MassMailController
from odoo.addons.auth_totp.controllers.home import TRUSTED_DEVICE_COOKIE,TRUSTED_DEVICE_AGE


class TwoFactorOutput(graphene.ObjectType):
    user = graphene.Field(lambda: User)
    key = graphene.String()
    value = graphene.String()
    max_age = graphene.Int()
    httponly = graphene.Boolean()
    samesite = graphene.String()


class LoginOutput(graphene.ObjectType):
    user = graphene.Field(lambda: User)
    cart = graphene.Field(lambda: Order)
    wishlist_items = graphene.List(WishlistItem)


class CheckoutRedirectOutput(graphene.ObjectType):
    access_token = graphene.String()


class Login(graphene.Mutation):
    class Arguments:
        email = graphene.String(required=True)
        password = graphene.String(required=True)
        subscribe_newsletter = graphene.Boolean(default_value=False)

    Output = LoginOutput

    @staticmethod
    def mutate(self, info, email, password, subscribe_newsletter):
        env = info.context['env']
        website = env['website'].get_current_website()

        # Set email in lowercase
        email = email.lower()

        try:
            uid = request.session.authenticate(request.session.db, email, password)
            user = env['res.users'].sudo().browse(uid)

            if bool(user._mfa_type()):
                cookies = request.httprequest.cookies
                key = cookies.get(TRUSTED_DEVICE_COOKIE)
                if key:
                    user_match = request.env['auth_totp.device']._check_credentials_for_uid(
                        scope="browser", key=key, uid=user.id)
                    if user_match:
                        request.session.finalize(request.env)

            # Subscribe Newsletter
            if website.vsf_mailing_list_id and subscribe_newsletter:
                MassMailController().subscribe(website.vsf_mailing_list_id.id, email, 'email')

            wishlist_items = env['product.wishlist'].sudo().search([
                ('partner_id', '=', user.partner_id.id), ('website_id', '=', website.id)])
            wishlist_items = wishlist_items.filtered(
                lambda wish:
                wish.sudo().product_id.product_tmpl_id.website_published
                and wish.sudo().product_id.product_tmpl_id._can_be_added_to_cart()
            )

            return LoginOutput(
                user=user,
                cart=website.sale_get_order(force_create=True),
                wishlist_items=wishlist_items,
            )

        except odoo.exceptions.AccessDenied as e:
            if e.args == odoo.exceptions.AccessDenied().args:
                raise GraphQLError(_('Wrong email or password.'))
            else:
                raise GraphQLError(_(e.args[0]))


class Logout(graphene.Mutation):

    Output = graphene.Boolean

    @staticmethod
    def mutate(self, info):
        request.session.logout()
        return True


class Register(graphene.Mutation):
    class Arguments:
        name = graphene.String(required=True)
        email = graphene.String(required=True)
        password = graphene.String(required=True)
        subscribe_newsletter = graphene.Boolean(default_value=False)

    Output = User

    @staticmethod
    def mutate(self, info, name, email, password, subscribe_newsletter):
        env = info.context['env']
        website = env['website'].get_current_website()

        # Set email in lowercase
        email = email.lower()

        data = {
            'name': name,
            'login': email,
            'password': password,
        }

        if env['res.users'].sudo().search([('login', '=', data['login'])], limit=1):
            raise GraphQLError(_('Another user is already registered using this email address.'))

        env['res.users'].sudo().signup(data)

        # Subscribe Newsletter
        if website and website.vsf_mailing_list_id and subscribe_newsletter:
            MassMailController().subscribe(website.vsf_mailing_list_id.id, email, 'email')

        return env['res.users'].sudo().search([('login', '=', data['login'])], limit=1)


class ResetPassword(graphene.Mutation):
    class Arguments:
        email = graphene.String(required=True)

    Output = User

    @staticmethod
    def mutate(self, info, email):
        env = info.context['env']
        ResUsers = env['res.users'].sudo()
        create_user = info.context.get('create_user', False)

        # Set email in lowercase
        email = email.lower()

        user = ResUsers.search([('login', '=', email)])
        if not user:
            user = ResUsers.search([('email', '=', email)])
        if len(user) != 1:
            raise GraphQLError(_('Invalid email.'))

        try:
            user.with_context(create_user=create_user).api_action_reset_password()
            return user
        except UserError as e:
            raise GraphQLError(e.name or e.value)
        except SignupError:
            raise GraphQLError(_('Could not reset your password.'))
        except Exception as e:
            raise GraphQLError(str(e))


class ChangePassword(graphene.Mutation):
    class Arguments:
        token = graphene.String(required=True)
        new_password = graphene.String(required=True)

    Output = User

    @staticmethod
    def mutate(self, info, token, new_password):
        env = info.context['env']

        data = {
            'password': new_password,
        }

        ResUsers = env['res.users'].sudo()

        try:
            login, password = ResUsers.signup(data, token)
            return ResUsers.search([('login', '=', login)], limit=1)
        except UserError as e:
            raise GraphQLError(e.args[0])
        except SignupError:
            raise GraphQLError(_('Could not change your password.'))
        except Exception as e:
            raise GraphQLError(str(e))


class UpdatePassword(graphene.Mutation):
    class Arguments:
        current_password = graphene.String(required=True)
        new_password = graphene.String(required=True)

    Output = User

    @staticmethod
    def mutate(self, info, current_password, new_password):
        env = info.context['env']
        website = env['website'].get_current_website()
        website_user = website.user_id
        if env.uid:
            user = env['res.users'].sudo().search([('id', '=', env.uid), ('active', 'in', [True, False])], limit=1)

            # Prevent "Public User" to be Updated
            if user and user.id and user.id == website_user.id:
                raise GraphQLError(_('Partner cannot be updated.'))
            try:
                user._check_credentials(current_password, env)
                user.change_password(current_password, new_password)
                env.cr.commit()
                request.session.authenticate(request.session.db, user.login, new_password)
                if bool(user._mfa_type()):
                    request.session.finalize(request.env)
                return user
            except Exception as e:
                raise GraphQLError(_('Incorrect password.'))
        else:
            raise GraphQLError(_('You must be logged in.'))




class TotpVerification(graphene.Mutation):
    class Arguments:
        code = graphene.String(required=True)
        user_id = graphene.Int(required=True)
        remember_device = graphene.Boolean(default_value=False)

    Output = TwoFactorOutput

    @staticmethod
    def mutate(self, info, code, user_id, remember_device):
        env = info.context['env']
        website = env['website'].get_current_website()
        request.website = website
        user = env['res.users'].sudo().search([('id', '=', user_id)], limit=1)
        key = False

        try:
            with user._assert_can_auth(user=user_id):
                user._totp_check(int(re.sub(r'\s', '', code)))
        except AccessDenied as e:
            raise GraphQLError(_(str(e)))
        except ValueError:
            raise GraphQLError(_("Invalid authentication code format."))

        request.session.finalize(request.env)
        request.update_env(user=request.session.uid)
        request.update_context(**request.session.context)
        if remember_device:
            name = _("%(browser)s on %(platform)s",
                     browser=request.httprequest.user_agent.browser.capitalize(),
                     platform=request.httprequest.user_agent.platform.capitalize(),
                     )

            if request.geoip.city.name:
                name += f" ({request.geoip.city.name}, {request.geoip.country_name})"

            key = request.env['auth_totp.device']._generate("browser", name)

        return TwoFactorOutput(user=user,
                               key=TRUSTED_DEVICE_COOKIE,
                               value=key,
                               max_age=TRUSTED_DEVICE_AGE,
                               httponly=True,
                               samesite='Lax')


class CheckoutRedirect(graphene.Mutation):
    class Arguments:
        session_id = graphene.String(required=True)

    Output = CheckoutRedirectOutput

    @staticmethod
    def mutate(self, info, session_id=None):
        # Always return an access token regardless of session validity
        # to avoid exposing whether a given session_id exists (prevents session probing)
        access_token = str(uuid.uuid4())

        if session_id:
            try:
                session = http.root.session_store.get(session_id)
                if session and session.get('sale_order_id'):
                    redis_client = info.context['env']['website']._redis_connect()
                    pipe = redis_client.pipeline()
                    pipe.set(access_token, session_id, ex=60)  # 60-second TTL
                    pipe.execute()
            except:
                pass

        return CheckoutRedirectOutput(access_token=access_token)


class SignMutation(graphene.ObjectType):
    login = Login.Field(description='Authenticate user with email and password and retrieves token.')
    logout = Logout.Field(description='Logout user')
    register = Register.Field(description='Register a new user with email, name and password.')
    reset_password = ResetPassword.Field(description="Send change password url to user's email.")
    change_password = ChangePassword.Field(description="Set new user's password with the token from the change "
                                                       "password url received in the email.")
    update_password = UpdatePassword.Field(description="Update user password.")
    totp_verification = TotpVerification.Field(description="Two-Factor Verification")
    checkout_redirect = CheckoutRedirect.Field(description="Returns access token to redirect user to Odoo checkout")
