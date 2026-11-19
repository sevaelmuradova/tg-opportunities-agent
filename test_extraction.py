"""Run: python test_extraction.py  (calls the real Gemini API)"""
import asyncio
from extract import extract_opportunity

OXFORD_ESSAY_PRIZE = """The Oxford Essay Prize

Davlat: Buyuk Britaniya 🇬🇧
Musobaqa shakli: Onlayn
Yosh toifasi: 14-18

The Oxford Essay Prize 14-18 yoshdagi o'quvchilarni o'zlari uchun muhim mavzularda esse yozishga, turli fanlarni bog'lashga va fikrlarini aniq dalillar bilan himoya qilishga chorlaydi. Ishlarni Oxford, Cambridge va MIT vakillaridan iborat xalqaro hay'at baholaydi. Tanlov onlayn o'tadi va barcha davlatlar uchun ochiq.

➡️Imtiyozlari:
- Jami 9,000£ dan oshiq pul mukofotlari
- Oxford Center for Advanced Research dasturiga stipendiyalar
- 15-noyabrgacha topshirganlarga qo'shimcha 100£ bonus
- Xalqaro e'tirof

🔗Ro'yxatdan o'tish uchun: Havola (https://apply.globalessayprize.uk/registration)

📌Ro'yxatdan o'tishning so'nggi muddati: 3-yanvar

⚡️@EduGrandsUz
"""

REMINDER = """
🎓Bu hafta muddati tugaydigan dasturlar ro’yxati:

- “Raqamli Avlod qizlari” Respublika Tanlovi 🇺🇿

- Horizon Academic Research Program 🇺🇸

- G’oyaLab tanlovi 🇺🇿

Imkoniyatdan foydalanib qoling. Tanlov o’z qo’lingizda!⚡️

⚡️@EduGrandsUz
"""
# (name, post, expected field values)
CASES = [
    ("oxford essay prize (real post)", OXFORD_ESSAY_PRIZE,
     {"is_opportunity": True, "type": "competition", "original_language": "uz", "apply_url": "https://apply.globalessayprize.uk/registration"}),
     ("Bu hafta muddati tugaydigan dasturlar ro’yxati: (real post)", REMINDER,
     {"is_opportunity": False, "type": "reminder", "original_language": "uz", "apply_url": None}),
]


async def main() -> None:
    results = await asyncio.gather(*(extract_opportunity(post) for _, post, _ in CASES))

    for (name, _, expected), result in zip(CASES, results):
        print(f"\n===== {name} =====")
        print(result.model_dump_json(indent=2))
        for field, want in expected.items():
            got = getattr(result, field)
            if isinstance(got, str) and isinstance(want, str):
                ok = got.strip().lower() == want.lower()
            else:
                ok = got == want
            print(f"  {'PASS' if ok else 'FAIL'}  {field}: expected {want!r}, got {got!r}")


if __name__ == "__main__":
    asyncio.run(main())
