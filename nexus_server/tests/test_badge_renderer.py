import unittest
from xml.etree import ElementTree as ET
from apps.common.publisher_badges import render_badge


class BadgeRendererTests(unittest.TestCase):
    def test_publication_badges_are_deterministic_and_distinct(self):
        first = render_badge(identity='first', name='Example')
        self.assertEqual(first, render_badge(identity='first', name='Example'))
        self.assertNotEqual(first, render_badge(identity='second', name='Example'))

    def test_badges_escape_names_and_reject_external_and_svg_logos(self):
        for logo in ['https://example.invalid/logo.png', 'data:image/svg+xml;base64,PHN2Zz4=']:
            image = render_badge(identity='one', name='<script>&\x00', logo=logo)
            self.assertNotIn(logo, image)
            self.assertNotIn('<script>', image)
            self.assertIn('&lt;script&gt;', image)
            ET.fromstring(image)

    def test_compact_and_card_support_unicode_and_publisher_counts(self):
        for style in ['compact', 'card']:
            image = render_badge(identity='org', name='发布者', publisher=True, count=3, style=style)
            ET.fromstring(image)
            self.assertIn('3 published Agents', image)
