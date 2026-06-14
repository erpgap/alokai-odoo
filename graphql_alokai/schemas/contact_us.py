# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene

from odoo.tools.mail import plaintext2html

from odoo.addons.graphql_alokai.schemas.objects import Lead
from odoo.addons.graphql_alokai.graphql.registry import mutation_registry


class ContactusAttachmentInput(graphene.InputObjectType):
    name = graphene.String(required=True)
    file_data = graphene.String(required=True)


class ContactUsParams(graphene.InputObjectType):
    name = graphene.String(required=True)
    email = graphene.String(required=True)
    phone = graphene.String(required=True)
    company = graphene.String()
    subject = graphene.String(required=True)
    message = graphene.String(required=True)
    attachments = graphene.List(ContactusAttachmentInput, default_value={})

class ContactUs(graphene.Mutation):
    class Arguments:
        contactus = ContactUsParams()

    Output = Lead

    @staticmethod
    def mutate(self, info, contactus):
        env = info.context['env']

        data = {
            'contact_name': contactus['name'],
            'email_from': contactus['email'],
            'phone': contactus['phone'],
            'name': contactus['subject'],
            'description': plaintext2html(contactus['message']),
        }

        # If Contact Us have one Company Name
        if contactus.get('company'):
            company = {'partner_name': contactus['company']}
            data.update(company)

        CrmLead = env['crm.lead'].sudo()
        lead =  CrmLead.create(data)

        attachments = env['ir.attachment']
        for file_upload in contactus.get('attachments', []):
            attachments |= env['ir.attachment'].sudo().create({
                'name': file_upload['name'],
                'datas': file_upload['file_data'].encode(),
                'res_model': CrmLead._name,
                'res_id': lead.id,
            })
        if attachments:
            lead.message_post(attachment_ids=attachments.ids)
        return lead


class ContactUsMutation(graphene.ObjectType):
    contact_us = ContactUs.Field(description='Creates a new lead with the contact information.')

mutation_registry.append(ContactUsMutation)
