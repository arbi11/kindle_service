"""Clearly labelled, offline examples. Never included in automatic delivery."""
from .content import paragraphs_html


def seed(store):
    if store.query("SELECT 1 FROM editions LIMIT 1", one=True):
        return
    examples = [
        ("article", "Make a little room for a good read", "A note from Daily Edition",
         "A small daily edition can turn a growing list of things to catch up on into something you actually enjoy reading.",
         ["There is a particular kind of optimism in saving something to read later. You spot a thoughtful essay, or a video from someone whose work you trust, and imagine a quieter moment in which to give it your attention.",
          "Daily Edition is built for that moment. Choose the channels you care about, bring in the articles you have saved, and let your computer gather a manageable edition. Read here, receive it as a newsletter, or take it to your Kindle.",
          "Start small. Follow two or three channels and one Raindrop collection. Choose enough reading for the time you actually have. You can always add more sources later.",
          "Your library belongs on your device. If you enable AI summaries, the source text goes directly to the provider you choose, using your own API key. There is no Daily Edition account to create.",
          "This is a sample article, included to help you try the reader. When you add your own reading, your library will make room for it."]),
        ("video", "A summary that keeps the story intact", "Sample video summary",
         "A useful summary preserves the explanation, the evidence and the caveats, while giving you time back.",
         ["[0:00] A good explanation does more than list facts. It begins with a question, builds a case, and arrives somewhere. When a video becomes reading material, that progression should survive.",
          "[2:30] Daily Edition asks your selected model to keep concrete details: the numbers that support an argument, the examples that make an idea understandable, and the limits that stop a conclusion from sounding more certain than it is.",
          "[5:10] Your interests guide where the summary spends its words. The source remains the authority. Timestamp links in summaries of real videos let you return to the relevant moment and hear it for yourself.",
          "[7:45] You choose the length. A short edition can offer a useful overview; a longer one can preserve more of the original reasoning. Either way, the aim is a complete, readable story.",
          "This is an illustrative sample, not a summary of an actual video. Connect a YouTube channel in Sources to prepare your own edition."]),
        ("article", "Your reading, at your pace", "A note on the routine",
         "Start with a small edition and a delivery time that fits the day you want to have.",
         ["A reading habit works best when it asks something reasonable of you. Twenty minutes of good material is often more inviting than an endless feed.",
          "In Settings, set your reading budget and how much detail you want in each summary. In Delivery, choose when an edition should be prepared and whether it should arrive by email or on your Kindle.",
          "The laptop does the work, so it needs to be awake and connected. If it misses a scheduled time, it can catch up when you next open the app. You can also prepare an edition whenever you like.",
          "There is no reward for clearing everything. Save a piece you want to return to, mark something read when you have finished, and let the rest wait."]),
    ]
    ids = []
    for i, (kind, title, author, takeaway, paragraphs) in enumerate(examples):
        body = paragraphs_html(paragraphs)
        ids.append(store.add_item(kind=kind, external_id=f"sample-{i}", title=title, author=author,
                                 body_html=body, summary_html=body if kind == "video" else "",
                                 takeaway=takeaway, is_sample=True, connection_id="youtube" if kind == "video" else "raindrop"))
    store.make_edition("A little less scrolling. A little more reading.", ids, is_sample=True)
