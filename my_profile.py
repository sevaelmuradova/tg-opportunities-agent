from models import UserProfile

MY_PROFILE = UserProfile(
    location="Uzbekistan",
    age=19,
    education_level="gap_year",
    fields_of_interest=[
        "ML", "AI", "NLP", "CS", "telecommunications", "entrepreneurship", "research opportunities",
    ],
    skills=["Python", "LLM inference", "Whisper", "ML engineering"],
    languages=["Uzbek", "Russian", "English"],
    max_application_fee=15.0,
    no_fee_preferred=True,
    goals=(
        "Looking for international ML/CS programs, research opportunities, competitions, and "
        "startup-related opportunities. Currently on a gap year, interested in anything that builds "
        "toward university applications or professional ML experience."
    ),
)
