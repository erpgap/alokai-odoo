# -*- coding: utf-8 -*-
import graphene
from odoo import _
from odoo.addons.graphql_alokai.schemas.objects import (
    BlogPost,
    BlogTag,
    SortEnum,
    get_document_with_check_access,
)
from odoo.osv import expression

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


class BlogTags(graphene.Interface):
    blog_tags = graphene.List(BlogTag)
    total_count = graphene.Int(required=True)


class BlogTagList(graphene.ObjectType):
    class Meta:
        interfaces = (BlogTags,)


class BlogPosts(graphene.Interface):
    blog_posts = graphene.List(BlogPost)
    blog_tags = graphene.List(BlogTag)
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
    blog_tags = graphene.Field(
        BlogTags,
    )
    blog_post = graphene.Field(
        BlogPost,
        required=True,
        id=graphene.Int(),
        slug=graphene.String(default_value=None),
    )
    blog_posts = graphene.Field(
        BlogPosts,
        filter=graphene.Argument(BlogPostFilterInput, default_value={}),
        current_page=graphene.Int(default_value=1),
        page_size=graphene.Int(default_value=10),
        search=graphene.String(default_value=False),
        sort=graphene.Argument(BlogPostSortInput, default_value={})
    )

    @staticmethod
    def resolve_blog_tags(self, info):
        env = info.context['env']
        BlogPost = env['blog.post']
        blog_posts = get_document_with_check_access(BlogPost, [], limit=0)
        blog_posts=blog_posts and blog_posts.sudo() or blog_posts
        blog_tags = blog_posts.mapped('tag_ids').sorted(key=lambda b: (b.name, b.id))
        total_count = len(blog_tags)
        return BlogTagList(blog_tags=blog_tags, total_count=total_count)

    @staticmethod
    def resolve_blog_post(self, info, id=None, slug=None):
        BlogPost = info.context['env']['blog.post']

        if id:
            domain = [('id', '=', id)]
            blog_post = get_document_with_check_access(BlogPost, domain)
        elif slug:
            domain = [('website_slug', '=', slug)]
            blog_post = get_document_with_check_access(BlogPost, domain)
        else:
            blog_post = BlogPost

        return blog_post.sudo()

    @staticmethod
    def resolve_blog_posts(self, info, filter, current_page, page_size, search, sort):
        env = info.context["env"]
        sort_order = get_search_order(sort)
        domain = []

        if search:
            for srch in search.split(" "):
                domain.append([
                    '|', ('name', 'ilike', srch), ('content', 'like', srch)])

        # Filter by stages or default to sales and done
        if filter.get('tag_id', False):
            domain.append([('tag_ids', 'in', filter['tag_id'])])

        # First offset is 0 but first page is 1
        if current_page > 1:
            offset = (current_page - 1) * page_size
        else:
            offset = 0

        BlogPost = env['blog.post']
        blog_posts = get_document_with_check_access(BlogPost, expression.AND(domain), sort_order, limit=0)
        blog_posts=blog_posts and blog_posts.sudo() or blog_posts
        total_count = len(blog_posts)
        blog_tags = blog_posts.mapped('tag_ids').sorted(key=lambda b: (b.name, b.id))
        blog_posts = blog_posts[offset:offset + page_size]
        return BlogPostList(blog_posts=blog_posts, blog_tags=blog_tags, total_count=total_count)
