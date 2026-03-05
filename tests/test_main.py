from src.main import hello


def test_hello_returns_greeting():
    result = hello("World")

    assert result == "Hello, World!"


def test_hello_with_empty_string():
    result = hello("")

    assert result == "Hello, !"
