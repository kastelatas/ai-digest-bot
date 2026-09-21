import unittest

from digest_bot.dedup import find_duplicate, is_duplicate, normalize_title, similarity


class DedupTests(unittest.TestCase):
    def test_identical_titles_are_duplicates(self):
        self.assertTrue(is_duplicate("OpenAI выпустила новую модель", ["OpenAI выпустила новую модель"]))

    def test_reworded_headline_is_duplicate(self):
        a = "OpenAI представила новую модель GPT для разработчиков"
        b = "OpenAI представила новую модель GPT разработчикам"
        self.assertGreaterEqual(similarity(a, b), 0.82)
        self.assertTrue(is_duplicate(a, [b]))

    def test_different_stories_are_not_duplicates(self):
        a = "OpenAI выпустила новую модель"
        b = "Google закрыл проект беспилотных такси в Европе"
        self.assertFalse(is_duplicate(a, [b]))

    def test_empty_recent_list_never_duplicate(self):
        self.assertFalse(is_duplicate("Любой заголовок", []))

    def test_normalize_title_strips_punctuation_and_case(self):
        self.assertEqual(normalize_title("OpenAI: Новая Модель!"), "openai новая модель")

    def test_find_duplicate_returns_best_match(self):
        recent = ["Совершенно другая новость", "OpenAI выпустила новую модель для разработчиков"]
        match = find_duplicate("OpenAI выпустила новую модель разработчикам", recent)
        self.assertEqual(match, "OpenAI выпустила новую модель для разработчиков")


if __name__ == "__main__":
    unittest.main()
