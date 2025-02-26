# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphql import GraphQLError
from odoo import _
from odoo.http import request

from odoo.addons.graphql_vuestorefront.schemas.objects import Partner


class UserProfileQuery(graphene.ObjectType):
    partner = graphene.Field(
        Partner,
        required=True,
    )

    @staticmethod
    def resolve_partner(self, info):
        uid = request.session.uid
        user = info.context['env']['res.users'].sudo().browse(uid)
        if user:
            partner = user.partner_id
            if not partner:
                raise GraphQLError(_('Partner does not exist.'))
        else:
            raise GraphQLError(_('User does not exist.'))
        return partner


class UpdateMyAccountParams(graphene.InputObjectType):
    # Deprecated
    id = graphene.Int()
    name = graphene.String()
    email = graphene.String()


class UpdateMyAccount(graphene.Mutation):
    class Arguments:
        myaccount = UpdateMyAccountParams()
        current_password = graphene.String()

    Output = Partner

    @staticmethod
    def mutate(self, info, myaccount, current_password=''):
        env = info.context["env"]
        website = env['website'].get_current_website()
        user = request.env.user
        website_user = website.user_id

        # Prevent "Public User" to be Updated
        if user.id == website_user.id:
            raise GraphQLError(_('Partner cannot be updated.'))

        partner = user.partner_id
        if not partner:
            raise GraphQLError(_('Partner does not exist.'))
        if myaccount.get('email') and partner.email != myaccount['email']:
            if not current_password:
                raise GraphQLError(_('Password is required to change email.'))
            try:
                partner.write(myaccount)
                user._check_credentials(current_password, env)
                if myaccount.get('email'):
                    user.login = myaccount['email']
                env.cr.commit()
                request.session.authenticate(request.session.db, user.login, current_password)
                if bool(user._mfa_type()):
                    request.session.finalize(request.env)
                return partner
            except Exception as e:
                raise GraphQLError(_('Incorrect password.'))
        else:
            partner.write(myaccount)
            return partner

class UserProfileMutation(graphene.ObjectType):
    update_my_account = UpdateMyAccount.Field(description='Update MyAccount')
