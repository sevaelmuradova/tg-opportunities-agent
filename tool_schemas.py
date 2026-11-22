"""Gemini function declarations for the agent's tools (OpenAPI-style schema, as Gemini expects).

user_id is deliberately NOT a parameter of any tool: the agent injects the real user_id in code,
so the model can never read or change another user's data.
"""
from typing import get_args

from models import OpportunityType, UserProfile

OPPORTUNITY_TYPES = list(get_args(OpportunityType))
PROFILE_FIELDS = list(UserProfile.model_fields)

OPPORTUNITY_ID = {
    "type": "INTEGER",
    "description": "The opportunity's ID. '#3', 'number 3' or 'opportunity 3' all mean 3.",
}

FUNCTION_DECLARATIONS = [
    {
        "name": "search_opportunities",
        "description": "Search stored opportunities by keyword, optionally filtered by type and minimum "
        "relevance score. Returns the top 5 matches, best first. Use for 'show me / find / any ... ?'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "1-3 short English keywords for the topic, e.g. 'ML', 'NLP research', "
                    "'startup'. Empty string to list the best matches regardless of topic. Don't repeat "
                    "the type here.",
                },
                "type": {
                    "type": "STRING",
                    "enum": OPPORTUNITY_TYPES,
                    "nullable": True,
                    "description": "Only this kind of opportunity; null for any kind.",
                },
                "min_score": {
                    "type": "INTEGER",
                    "description": "Minimum relevance score 0-100 (default 50). Lower it if the user "
                    "asks for everything.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_saved",
        "description": "List the opportunities the user has saved.",
    },
    {
        "name": "get_deadlines",
        "description": "Upcoming deadlines of the user's SAVED opportunities within the next N days.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "days": {
                    "type": "INTEGER",
                    "description": "How many days ahead to look (default 7; 'this week' = 7, "
                    "'this month' = 30). Use 30 if the user doesn't give a period.",
                },
            },
        },
    },
    {
        "name": "get_opportunity_details",
        "description": "Full details of one opportunity: requirements, benefits, fee, link, deadline.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"opportunity_id": OPPORTUNITY_ID},
            "required": ["opportunity_id"],
        },
    },
    {
        "name": "save_opportunity",
        "description": "Save an opportunity for later (bookmark it).",
        "parameters": {
            "type": "OBJECT",
            "properties": {"opportunity_id": OPPORTUNITY_ID},
            "required": ["opportunity_id"],
        },
    },
    {
        "name": "dismiss_opportunity",
        "description": "Dismiss an opportunity the user is not interested in; it stops appearing in "
        "searches and saved lists.",
        "parameters": {
            "type": "OBJECT",
            "properties": {"opportunity_id": OPPORTUNITY_ID},
            "required": ["opportunity_id"],
        },
    },
    {
        "name": "update_profile",
        "description": "Change one field of the user's profile. For list fields (fields_of_interest, "
        "skills, languages) the value is ADDED to the list; other fields are replaced.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "field": {"type": "STRING", "enum": PROFILE_FIELDS},
                "value": {
                    "type": "STRING",
                    "description": "The new value / item to add, in English, e.g. 'forensics', "
                    "'Kazakhstan', '20'. For max_application_fee a USD number or 'none' for no limit.",
                },
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "general_question",
        "description": "Advice and explanations rather than a lookup or change of stored data: "
        "'what's good for gap year students?', 'what kind of opportunities suit me?', 'how do I write "
        "a motivation letter?', greetings. The answer uses the user's profile.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "question": {"type": "STRING", "description": "The user's question, as asked."},
            },
            "required": ["question"],
        },
    },
]
