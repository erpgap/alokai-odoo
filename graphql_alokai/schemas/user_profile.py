# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from graphql import GraphQLError
from odoo import _, SUPERUSER_ID
from odoo.http import request

from odoo.addons.graphql_alokai.schemas.objects import Partner


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

    Output = Partner

    @staticmethod
    def mutate(self, info, myaccount):
        env = info.context["env"]
        website = env['website'].get_current_website()
        user = request.env.user
        website_user = website.user_id

        # Prevent "Public User" to be Updated
        if user.id == website_user.id:
            raise GraphQLError(_('Partner cannot be updated.'))

        partner = user.partner_id
        if partner:
            partner.write(myaccount)
        else:
            raise GraphQLError(_('Partner does not exist.'))
        return partner


class DeleteMyAccount(graphene.Mutation):

    Output = graphene.Boolean

    @staticmethod
    def mutate(self, info):
        env = info.context["env"]
        website = env['website'].get_current_website()
        request.website = website
        user = request.env.user.sudo()
        website_user = website.user_id

        # Prevent "Public User" to be deleted
        if user.id == website_user.id or not user.has_group('base.group_portal'):
            raise GraphQLError(_('User cannot be deleted.'))

        if user:
            request.session.logout()
            if not user.email:
                raise GraphQLError(_(f"Cannot send email: user {user.name} has no email address."))
            user._deactivate_portal_user()
            template = env.ref('graphql_alokai.delete_my_account_email').sudo()
            template.send_mail(user.id, raise_exception=True, force_send=True)
        else:
            raise GraphQLError(_('User does not exist.'))
        return True


class UserProfileMutation(graphene.ObjectType):
    update_my_account = UpdateMyAccount.Field(description='Update MyAccount')
    delete_my_account = DeleteMyAccount.Field(description='Delete MyAccount')