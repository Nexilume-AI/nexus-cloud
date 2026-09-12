"""The original shared raster/SSRF assertions, without an Enterprise gateway host."""
from django.test import SimpleTestCase
from tests.image_media_guards import ImageSafetyGuards


class PersonalImageSafetyTests(ImageSafetyGuards, SimpleTestCase):
    pass
