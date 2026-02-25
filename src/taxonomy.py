"""
Sync Licensing Standard Taxonomy
Based on DISCO.ac and Universal Production Music (UPPM) tagging standards.

These lists are used as the text prompts for CLAP zero-shot audio classification
and as the constrained vocabulary for LLM semantic synthesis.
"""

GENRES = [
    "Acoustic",
    "Ambient",
    "Blues",
    "Cinematic",
    "Classical",
    "Country",
    "Dance",
    "Drum and Bass",
    "Dubstep",
    "Electronic",
    "Experimental",
    "Folk",
    "Funk",
    "Hip Hop",
    "House",
    "Heavy Metal",
    "Indie Pop",
    "Indie Rock",
    "Industrial",
    "Jazz",
    "Lo-Fi",
    "Pop",
    "Punk",
    "R&B",
    "Rock",
    "Soul",
    "Soundtrack",
    "Synthwave",
    "Techno",
    "Trap",
    "World Music"
]

MOODS = [
    "Action",
    "Adventurous",
    "Aggressive",
    "Ambient",
    "Angry",
    "Beautiful",
    "Calm",
    "Cheerful",
    "Chill",
    "Cinematic",
    "Cool",
    "Dark",
    "Dramatic",
    "Dreamy",
    "Emotional",
    "Energetic",
    "Epic",
    "Happy",
    "Inspiring",
    "Melancholic",
    "Mysterious",
    "Playful",
    "Romantic",
    "Sad",
    "Scary",
    "Serious",
    "Suspenseful",
    "Tense",
    "Uplifting"
]

INSTRUMENTS = [
    "Acoustic Guitar",
    "Electric Guitar",
    "Bass Guitar",
    "Synth Bass",
    "Piano",
    "Synthesizer",
    "Strings",
    "Violin",
    "Cello",
    "Brass",
    "Trumpet",
    "Saxophone",
    "Woodwinds",
    "Acoustic Drums",
    "Electronic Drums",
    "Percussion",
    "808",
    "Female Vocals",
    "Male Vocals",
    "Choir"
]

TEMPOS = [
    "Very Slow",
    "Slow",
    "Medium",
    "Fast",
    "Very Fast"
]

# DISCO.ac specific "Type" tags
TRACK_TYPES = [
    "Cover",
    "Demo",
    "Easy-clear",
    "Focus track",
    "Mainstream",
    "One Stop",
    "Recognizable",
    "Rerecord",
    "Samples",
    "Score",
    "Sound design"
]

# Standard ID3v2 mapping fields used by DISCO
ID3_DISCO_MAPPING = [
    "Title",
    "Artist",
    "Album",
    "Grouping", # Often used for control details/rights
    "Composer",
    "Year",
    "Comments", # Often used for custom tags and contact/clearance info
    "Release Date",
    "Genre",
    "BPM",
    "ISRC"
]
