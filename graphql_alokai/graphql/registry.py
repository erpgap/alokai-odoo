# -*- coding: utf-8 -*-
# Copyright 2024 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).

import graphene
from odoo.addons.graphql_base import OdooObjectType

query_registry = []
mutation_registry = []
type_registry = []


class AlokaiObjectType(OdooObjectType):
    """
    Base class for all GraphQL ObjectTypes in the Alokai module.
    """

    @classmethod
    def add_field(cls, field_name, field_type, resolver=None):
        """
        Add a new field to the GraphQL ObjectType.

        :param field_name: Name of the field to add.
        :param field_type: Type of the field to add.
        :param resolver: Optional resolver function for the field.
        """

        # Remove existing field if it exists
        if field_name in cls._meta.fields:
            del cls._meta.fields[field_name]

        # Add new field
        cls._meta.fields[field_name] = graphene.Field(
            field_type,
            resolver=resolver,
            required=False
        )


def add_or_replace(registry, new_items):
    for new_item in new_items:
        new_name = new_item._meta.name
        for old_item in registry:
            if old_item._meta.name == new_name:
                registry.remove(old_item)
                break
        registry.append(new_item)


class BaseQuery(graphene.ObjectType):
    """Empty base query; extension modules can add fields."""


class BaseMutation(graphene.ObjectType):
    """Empty base mutation; extension modules can add mutations."""


def build_alokai_schema():
    """
    Dynamically build the final GraphQL schema by inheriting
    from all partial query and mutation classes in the registry.
    """

    Query = type(
        "Query",
        tuple(query_registry + [BaseQuery]),
        {}
    )

    Mutation = type(
        "Mutation",
        tuple(mutation_registry + [BaseMutation]),
        {}
    )

    return graphene.Schema(query=Query, mutation=Mutation, types=type_registry)
