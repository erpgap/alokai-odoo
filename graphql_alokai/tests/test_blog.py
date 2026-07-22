# -*- coding: utf-8 -*-
# Copyright 2021-2026 ERPGAP/PROMPTEQUATION LDA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl).
"""Correctness tests for the blog queries.

The blog list used to load every published post into memory and slice the page
in Python. These tests pin the observable contract of the DB-paginated version:
the total count, the page, disjoint pages, and — importantly — the tag cloud,
which must cover *all* matching posts, not just the page.

The scenarios are scoped with a unique tag so the assertions are deterministic
regardless of any demo blog content already in the database.
"""

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.graphql_alokai.schemas.website_blog import BlogPostQuery


class _Info:
    def __init__(self, env):
        self.context = {'env': env}


class _SortVal:
    """Stand-in for graphene's SortEnum member (only ``.value`` is read)."""
    def __init__(self, value):
        self.value = value


ASC = _SortVal('ASC')


@tagged('post_install', '-at_install', 'alokai_blog')
class TestBlogQueries(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.info = _Info(env)

        blog = env['blog.blog'].create({'name': 'ZZ Blog'})
        Tag = env['blog.tag']
        cls.ta = Tag.create({'name': 'ZZ Tag A'})
        cls.tb = Tag.create({'name': 'ZZ Tag B'})
        cls.tc = Tag.create({'name': 'ZZ Tag C'})

        def post(name, tags, published=True):
            return env['blog.post'].create({
                'name': name, 'blog_id': blog.id, 'is_published': published,
                'tag_ids': [(6, 0, [t.id for t in tags])],
            })

        # Three published posts carry Tag A (one also carries Tag B); plus
        # posts for B and C and an unpublished one that must never appear.
        cls.pa1 = post('ZZ Post A1', [cls.ta])
        cls.pa2 = post('ZZ Post A2', [cls.ta])
        cls.pa3 = post('ZZ Post A3', [cls.ta, cls.tb])
        cls.pb1 = post('ZZ Post B1', [cls.tb])
        cls.pc1 = post('ZZ Post C1', [cls.tc])
        cls.unpublished = post('ZZ Post Draft', [cls.ta], published=False)
        env.flush_all()

    def _posts(self, filter=None, page=1, page_size=10, search=False, sort=None):
        return BlogPostQuery.resolve_blog_posts(
            None, self.info, filter or {}, page, page_size, search, sort or {})

    # ------------------------------------------------------------------ #
    #  blog_posts                                                         #
    # ------------------------------------------------------------------ #
    def test_total_count_excludes_unpublished(self):
        result = self._posts(filter={'tag_id': [self.ta.id]})
        self.assertEqual(result.total_count, 3, "only the 3 published Tag-A posts")
        returned = {p.id for p in result.blog_posts}
        self.assertNotIn(self.unpublished.id, returned)

    def test_pagination_pages_are_disjoint(self):
        page1 = self._posts(filter={'tag_id': [self.ta.id]}, page=1, page_size=2,
                            sort={'id': ASC})
        page2 = self._posts(filter={'tag_id': [self.ta.id]}, page=2, page_size=2,
                            sort={'id': ASC})
        self.assertEqual(page1.total_count, 3)
        self.assertEqual(len(page1.blog_posts), 2, "first page is full")
        self.assertEqual(len(page2.blog_posts), 1, "second page has the remainder")
        ids1 = {p.id for p in page1.blog_posts}
        ids2 = {p.id for p in page2.blog_posts}
        self.assertFalse(ids1 & ids2, "pages must not overlap")

    def test_tag_cloud_covers_all_matching_posts_not_just_page(self):
        # Only 1 post per page, but the tag cloud must still contain every tag
        # that appears on any Tag-A post: A (all three) and B (on A3).
        result = self._posts(filter={'tag_id': [self.ta.id]}, page=1, page_size=1)
        self.assertEqual(len(result.blog_posts), 1, "page holds a single post")
        self.assertEqual({t.id for t in result.blog_tags}, {self.ta.id, self.tb.id})

    # ------------------------------------------------------------------ #
    #  blog_tags                                                          #
    # ------------------------------------------------------------------ #
    def test_blog_tags_lists_all_published_tags(self):
        result = BlogPostQuery.resolve_blog_tags(None, self.info)
        tag_ids = {t.id for t in result.blog_tags}
        # Our three tags are all in use on published posts.
        self.assertTrue({self.ta.id, self.tb.id, self.tc.id} <= tag_ids)
        self.assertEqual(result.total_count, len(result.blog_tags))
