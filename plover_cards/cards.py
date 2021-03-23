import sqlite3

import csv
from dataclasses import dataclass, field
from pathlib import Path
import re

from .anki_utils import get_models


def get_existing_notes(anki_path, note_type):
    models = get_models(anki_path)
    note_id = next((model.id for model in models if model.name == note_type))

    conn = sqlite3.connect(f"file:{anki_path}?mode=ro", uri=True)
    with conn:
        cursor = conn.cursor()

        cursor.execute("select sfld from notes where mid=?;", [note_id])
        notes = cursor.fetchall()

    return set(map(lambda r: r[0], notes))


def get_ignored_from_file(ignore_file):
    if not ignore_file.exists():
        return set()
    return set(ignore_file.read_text().splitlines())


def get_new_notes(new_notes_file):
    if not new_notes_file.exists():
        return {}

    lines = new_notes_file.read_text().splitlines()
    reader = csv.reader(lines)
    result = {}
    for line in reader:
        if len(line) == 2:
            result[line[0]] = line[1]

    return result


@dataclass
class Card:
    translation: str
    stroke_suggestions: list[str]
    frequency: int
    chosen_strokes: str = None
    ignored: bool = False
    similar_ignored: list[str] = field(default_factory=list)

    def choose_strokes(self, strokes):
        self.ignored = False
        self.chosen_strokes = strokes

    def ignore(self):
        self.ignored = True
        self.chosen_strokes = None

    def as_note(self):
        def escape(text):
            result = text.replace('"', '""')
            return f'"{result}"'

        return f"{escape(self.translation)},{escape(self.chosen_strokes)}"


def strokes_sort_key(strokes):
    num_strokes = strokes.count("/")
    return f"{num_strokes}{len(strokes)}{strokes}"


def similar_words(word):
    replacements = [
        ("s$", ""),
        ("es$", ""),
        ("ies$", "y"),
        ("ves$", "f"),
        ("ing$", ""),
        (".ing$", ""),
        ("ing$", "e"),
        ("ying$", "ie"),
        ("d$", ""),
        ("ed$", ""),
        ("ied$", "y"),
        ("eed$", "ee"),
    ]

    words = set()
    for replacement in replacements:
        (similar_word, count) = re.subn(replacement[0], replacement[1], word)
        if count > 0:
            words.add(similar_word)

    words.add(word.lower())

    return words


def create_cards(card_suggestions, ignored, new_notes):
    suggestions = card_suggestions.card_suggestions

    cards = []
    for phrase, data in suggestions.copy().items():
        if phrase in ignored:
            card_suggestions.delete(phrase)
        else:
            card = Card(
                translation=phrase,
                stroke_suggestions=sorted(list(data["strokes"]), key=strokes_sort_key),
                frequency=data["frequency"],
                chosen_strokes=new_notes.get(phrase, None),
                similar_ignored=list(similar_words(phrase).intersection(ignored)),
            )
            cards.append(card)

    return cards


class Cards:
    def __init__(self, config, card_suggestions):
        self.ignore_path = Path(config["paths"]["ignore"])
        self.output_path = Path(config["paths"]["output"])

        existing_notes = get_existing_notes(
            config["paths"]["anki_collection"], config["anki"]["note_type"]
        )
        self.ignored = get_ignored_from_file(self.ignore_path)

        new_notes = get_new_notes(self.output_path)
        self.cards = create_cards(
            card_suggestions,
            existing_notes.union(self.ignored),
            new_notes,
        )

        self.new_ignored = set()

    def __getitem__(self, index):
        return self.cards[index]

    def __len__(self):
        return len(self.cards)

    def choose_strokes(self, index, strokes):
        card = self.cards[index]
        card.choose_strokes(strokes)
        self.new_ignored.discard(card.translation)

    def ignore(self, index):
        card = self.cards[index]
        card.ignore()
        self.new_ignored.add(card.translation)

    def save(self):
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("a") as f:
            f.write(self._as_notes())
            f.write("\n")

        all_ignored = self.ignored.union(self.new_ignored)
        self.ignore_path.parent.mkdir(parents=True, exist_ok=True)
        self.ignore_path.write_text("\n".join(sorted(list(all_ignored))))

    def sort(self, *args, **kwargs):
        self.cards.sort(*args, **kwargs)

    def _as_notes(self):
        return "\n".join(
            [
                card.as_note()
                for card in self.cards
                if not card.ignored and card.chosen_strokes
            ]
        )
