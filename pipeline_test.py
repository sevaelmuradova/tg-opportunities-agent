"""Run: python pipeline_test.py  (Layer 1 extraction -> Layer 2 matching, calls the real Gemini API)"""
import asyncio

from extract import extract_opportunity
from matcher import match_opportunity
from my_profile import MY_PROFILE

# Paste the Telegram post between the triple quotes.
# Linked words lose their URL when copied - add it by hand, e.g. "Havola (https://...)".
POST = """
1 Billion AI Film Award 2027

Davlat: Xalqaro 🇦🇪
Dastur shakli: Onlayn
Yosh toifasi: 17 yoshdan katta

New Media Academy (1 Billion Followers Summit) tomonidan o'tkaziladigan bu onlayn tanlovda ijodkorlar Seedance yordamida yaratilgan 5 dan 7 daqiqagacha bo'lgan original qisqa metrajli AI filmlarini topshiradi.

➡️Imtiyozlari:
- Bosh sovrin 1 000 000 AQSh dollari
- Kirish va ariza topshirish mutlaqo bepul
- Bir nechta film topshirish mumkin, har biri alohida ariza bilan
- Sun'iy intellekt asosidagi kino sohasida o'z ishini xalqaro miqyosda ko'rsatish imkoniyati

🔗Ro'yxatdan o'tish uchun: https://www.1billionsummit.com/ai-award-2027

📌Ro'yxatdan o'tishning so'nggi muddati: 5-dekabr

⚡️@EduGrandsUz
"""


async def main() -> None:
    opportunity = await extract_opportunity(POST)
    print("===== OpportunityData (Layer 1) =====")
    print(opportunity.model_dump_json(indent=2))

    match = await match_opportunity(opportunity, MY_PROFILE)
    print("\n===== MatchResult (Layer 2) =====")
    print(match.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
