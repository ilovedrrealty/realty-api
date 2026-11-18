"""Pure wire and validation contracts for the two bounded map criteria."""

import unittest

from utils.search_geometry import INVALID_SHAPE, Polygon, Radius, parse_shape


class SearchGeometryTests(unittest.TestCase):
    def test_radius_wire_bounds_and_negative_zero(self):
        self.assertEqual(parse_shape("radius,-085.000000,0180,500.000000"), Radius(-85, 180, 500))
        self.assertEqual(parse_shape("radius,-0,-180,0.100000"), Radius(0, -180, 0.1))
        for shape in (
            "", "radius", "radius,85.000001,0,1", "radius,0,180.000001,1", "radius,0,0,500.000001",
            "radius,0,0,0.099999", "radius,+0,0,1", "radius,.5,0,1", "radius,1.,0,1", "radius,1e0,0,1",
            "radius,0,0,1.0000001", "radius,0,0,1\n", "radius,0, 0,1", "radius,０,0,1",
            "radius,nan,0,1", "radius,0,0,inf", "radius," + "0" * 601 + ",0,1",
        ):
            with self.subTest(shape=shape[:60]), self.assertRaisesRegex(ValueError, INVALID_SHAPE):
                parse_shape(shape)

    def test_polygons_reject_ambiguous_non_simple_paths(self):
        for shape in (
            "polygon,0,0,1,0", "polygon,0,0,1,0,0,1,2", "polygon,0,0,1,0,0,1,00,-0.000000",
            "polygon,0,0,0,1,0,2", "polygon,0,0,1,1,1,0,0,1",
            "polygon,0,0,0,3,3,3,0,1,3,0", "polygon,0,0,0,3,0,1,3,0",
            "polygon,-20,0,0,1,20,2",
            "polygon,0,-90.123456,0,89.876544,1,0",
            "polygon,0,-10.123456,2,169.876544,0,169.876544",
            "polygon,0,-179,1,179,0,179", "polygon" + ",0,0" * 25,
        ):
            with self.subTest(shape=shape), self.assertRaisesRegex(ValueError, INVALID_SHAPE):
                parse_shape(shape)

    def test_polygons_allow_concavity_and_tiny_nonzero_areas(self):
        for shape, count in (
            ("polygon,0,0,0,1,0,2,2,2,1,1,2,0", 6),
            ("polygon,19.750000,-70.450000,19.750000,-70.449999,19.750001,-70.450000", 3),
            ("polygon,84.999999,-70,85,-70,85,-69.999999", 3),
            ("polygon,60,-71,65,-70,70,-69", 3),
            ("polygon,0,-10.123456,1,169.876543,0,169.876543", 3),
        ):
            with self.subTest(shape=shape):
                result = parse_shape(shape)
                self.assertIsInstance(result, Polygon)
                self.assertEqual(len(result.projected), count)


if __name__ == "__main__":
    unittest.main()
