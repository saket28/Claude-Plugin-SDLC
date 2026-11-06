import unittest

import todo


class TodoTest(unittest.TestCase):
    def setUp(self):
        todo.reset()

    def test_add_and_list(self):
        i = todo.add("buy milk")
        self.assertEqual(todo.list_todos(), [{"id": i, "title": "buy milk"}])

    def test_empty_title_rejected(self):
        with self.assertRaises(ValueError):
            todo.add("  ")


if __name__ == "__main__":
    unittest.main()
