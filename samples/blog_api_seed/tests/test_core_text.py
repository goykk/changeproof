from blog.core.text import excerpt_words, slugify


def test_slugify():
    assert slugify("ChangeProof Impact Analysis") == "changeproof-impact-analysis"


def test_excerpt_words():
    assert excerpt_words("one two three four", 2) == "one two"