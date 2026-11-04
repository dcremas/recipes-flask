"""Site copy, kept out of the templates.

Wording carried over from the original recipes-heroku home page, with the
typos fixed ("their are" -> "there are", "worthly" -> "worthy",
"accompanyment" -> "accompaniment", "homeade" -> "homemade").

Every invitation to sign up and contribute has since been removed. The site is
read-and-print for visitors and authored only by the admin, so copy promising
otherwise was pointing at a door that is no longer there. Feedback is the one
thing a visitor can still send, and FEEDBACK below is a working form rather than
a description of one.
"""

# 2026-11-04 (owner's call, matching the main site): the copy no longer names
# the owner or the family. `owner` is used only for the footer copyright and
# the author meta tag, as on www.
SITE = {
    "name": "Recipes",
    "owner": "Dustin Cremascoli",
    "tagline": "Free to browse and print",
    "title": "Recipes — free to browse and print",
    "description": (
        "A free collection of tried-and-true home recipes. Browse any of them, "
        "and print the ones you want to cook."
    ),
    "url": "https://recipes.dustincremascoli.com",
    "main_site": "https://www.dustincremascoli.com",
    "socials": [
        {"label": "GitHub", "href": "https://github.com/dcremas", "icon": "github"},
        {
            "label": "LinkedIn",
            "href": "https://www.linkedin.com/in/dustin-cremascoli-662105423",
            "icon": "linkedin",
        },
    ],
}

# --------------------------------------------------------------------------- #
# The cross-site footer nav
# --------------------------------------------------------------------------- #
#
# The eight public properties served off the one EC2 box, in the order they
# appear in the SECOND row of every footer on the estate. The bottom of a page
# is a navigation surface, not a dead end: from here you can reach the main
# site, the charts, the SQL demo and the API without scrolling back up.
#
# `key` identifies the property and SITES_CURRENT names the one you are already
# on. That entry is still a link; it just carries `aria-current="page"` and a
# muted treatment, so the row is the same length on every site.
#
# Every entry opens in the same tab (estate link policy, 2026-10-13): this is a
# browsing site, so nothing is lost by leaving it. The same-origin href is
# RELATIVE so it stays on whichever host served the page, and the footer
# template compares it with request.path to decide whether aria-current is
# "page" (you are on "/") or "true" (you are elsewhere on this site).
#
# THIS LIST IS DUPLICATED seven times across five repos with no shared package.
# prosite_flask/content.py holds the canonical copy and names all seven;
# ../check-footer-nav.sh diffs them and exits non-zero when they disagree.
SITES = [
    {"key": "www", "label": "Main site", "href": "https://www.dustincremascoli.com/"},
    {
        "key": "viz",
        "label": "Data Viz",
        "href": "https://www.dustincremascoli.com/visualizations",
    },
    {"key": "sql", "label": "Ask Weather", "href": "https://sql.dustincremascoli.com/"},
    {
        "key": "api",
        "label": "Weather API",
        "href": "https://api.dustincremascoli.com/docs",
    },
    {"key": "pbp", "label": "Ask Football", "href": "https://pbp.dustincremascoli.com/"},
    {"key": "plays", "label": "Play Explorer", "href": "https://pbp.dustincremascoli.com/plays/"},
    {"key": "typing", "label": "PromptPace", "href": "https://typing.dustincremascoli.com/"},
    {"key": "recipes", "label": "Recipes", "href": "/"},
    # The three games joined the row 2026-10-25. They open in a new tab, the one
    # exception to the same-tab rule above, as they do on www.
    {"key": "mahjong", "label": "Mahjong Solitaire", "href": "https://mahjong.dustincremascoli.com/", "new_tab": True},
    {"key": "dtiles", "label": "D-Tiles", "href": "https://dtiles.dustincremascoli.com/", "new_tab": True},
    {"key": "solitaire", "label": "Solitaire", "href": "https://solitaire.dustincremascoli.com/", "new_tab": True},
    {"key": "sudoku", "label": "Sudoku", "href": "https://sudoku.dustincremascoli.com/", "new_tab": True},
]

# Which entry in SITES is this codebase.
SITES_CURRENT = "recipes"

SITES_LABEL = "Everything here"

HERO = {
    "greeting": "Tried-and-true recipes, free to browse and print.",
    "lede": "A free and full repository of favorite home recipes.",
    "body": (
        "Feel free to browse, print and use any recipe you see here. Nothing is "
        "gated and nothing needs an account — this is meant to be a free spot to "
        "discover and use our favorites."
    ),
}

FEATURES = [
    {
        "icon": "book",
        "title": "Browse the recipes",
        "body": "Take a look around and see whether there are any recipes that interest you.",
    },
    {
        "icon": "printer",
        "title": "Take a recipe",
        "body": (
            "Every recipe page produces a clean one-page PDF, ready to print or "
            "save and take into the kitchen."
        ),
    },
    {
        "icon": "list",
        "title": "Find it fast",
        "body": "Filter by category, or switch to the table view to size up the whole collection at once.",
    },
]

# The feedback section at the foot of the home page. Mirrors the contact block on
# dustincremascoli.com — same markup, same styles, same delivery chain.
#
# No email address since 2026-11-04: the form is the way in, as on the main
# site, which no longer publishes one either. (Never publish ADMIN_EMAIL, the
# sign-in address: it is half of the admin credential.)
FEEDBACK = {
    "heading": "Tell us how a recipe turned out.",
    "body": (
        "Found a quantity that looks wrong, a step that needs explaining, or a "
        "recipe that belongs here? Send it along."
    ),
    "socials": [
        {"label": "GitHub", "href": "https://github.com/dcremas", "icon": "github"},
        {
            "label": "LinkedIn",
            "href": "https://www.linkedin.com/in/dustin-cremascoli-662105423",
            "icon": "linkedin",
        },
    ],
}
