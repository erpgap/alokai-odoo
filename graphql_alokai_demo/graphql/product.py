# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
import logging
from odoo.addons.graphql_alokai.schemas.objects import User, Product
from odoo.addons.graphql_alokai.graphql.registry import type_registry

from odoo.addons.graphql_base import OdooObjectType

_logger = logging.getLogger(__name__)


# Declare the new Type
class ReviewType(OdooObjectType):
    id = graphene.Int(required=True)
    body = graphene.String()
    starred = graphene.Boolean()
    date = graphene.DateTime()
    author_id = graphene.Field(lambda: User)


# Monkey patch the Product Type to add the new field
def resolve_review_ids(self, info):
    return self.review_ids or []


# First, remove any existing entry for "review_ids" in Product._meta.fields
if "review_ids" in Product._meta.fields:
    del Product._meta.fields["review_ids"]


# Then, add the new field as a proper Field instance.
Product._meta.fields["review_ids"] = graphene.Field(
    graphene.List(graphene.NonNull(ReviewType)),
    resolver=resolve_review_ids,
)


# Register the new type in the registry
type_registry.append(ReviewType)
