# -*- coding: utf-8 -*-
import graphene
from graphql import GraphQLError

from odoo.http import request
from odoo import _
from odoo.addons.graphql_vuestorefront.schemas.objects import (
    BlogPost,
    SortEnum,
    get_document_with_check_access,
    get_document_count_with_check_access
)

def get_search_order(sort):
    sorting = ''
    for field, val in sort.items():
        if sorting:
            sorting += ', '
        sorting += '%s %s' % (field, val.value)

    # Add id as last factor, so we can consistently get the same results
    if sorting:
        sorting += ', id ASC'
    else:
        sorting = 'id ASC'

    return sorting


class BlogPosts(graphene.Interface):
    blog_posts = graphene.List(BlogPost)
    total_count = graphene.Int(required=True)


class BlogPostFilterInput(graphene.InputObjectType):
    tag_id = graphene.List(graphene.Int)


class BlogPostSortInput(graphene.InputObjectType):
    id = SortEnum()
    published_date = SortEnum()
    name = SortEnum()


class BlogPostList(graphene.ObjectType):
    class Meta:
        interfaces = (BlogPosts,)


class BlogPostQuery(graphene.ObjectType):
    blog_post = graphene.Field(
        BlogPost,
        required=True,
        id=graphene.Int(),
    )
    blog_posts = graphene.Field(
        BlogPosts,
        filter=graphene.Argument(BlogPostFilterInput, default_value={}),
        current_page=graphene.Int(default_value=1),
        page_size=graphene.Int(default_value=10),
        sort=graphene.Argument(BlogPostSortInput, default_value={})
    )

    @staticmethod
    def resolve_blog_post(self, info, id):
        BlogPost = info.context['env']['blog.post']
        error_msg = 'Blog Post does not exist.'
        blog_post = get_document_with_check_access(BlogPost, [('id', '=', id)], error_msg=error_msg)
        if not blog_post:
            raise GraphQLError(_(error_msg))
        return blog_post.sudo()

    @staticmethod
    def resolve_blog_posts(self, info, filter, current_page, page_size, sort):
        env = info.context["env"]
        user = request.env.user
        sort_order = get_search_order(sort)
        domain = []

        # Filter by stages or default to sales and done
        if filter.get('tag_id', False):
            domain += [('tag_ids', 'in', filter['tag_id'])]

        # First offset is 0 but first page is 1
        if current_page > 1:
            offset = (current_page - 1) * page_size
        else:
            offset = 0

        BlogPost = env['blog.post']
        blog_posts = get_document_with_check_access(BlogPost, domain, sort_order, page_size, offset,
                                                error_msg='Blog Post  does not exist.')
        total_count = get_document_count_with_check_access(BlogPost, domain)
        return BlogPostList(blog_posts=blog_posts and blog_posts.sudo() or blog_posts, total_count=total_count)