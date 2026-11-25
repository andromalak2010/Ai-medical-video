# AI Medical Video Production / إنتاج فيديوهات طبية بالذكاء الاصطناعي

A faceless social media video system for medical education: short explainer videos made with AI tools, without showing the creator's face or voice.

نظام لإنتاج فيديوهات تعليمية طبية قصيرة للسوشيال ميديا بالذكاء الاصطناعي، من غير ما يظهر وجه صانع المحتوى أو صوته.

---

## Goals / الأهداف

- Create realistic AI-generated medical explainer videos.
- Automate as much of the workflow as possible.
- Use free tools and free plans where possible.
- Work entirely from an iPad, through Claude Code on the web.

- إنتاج فيديوهات شرح طبية بالذكاء الاصطناعي تبدو واقعية.
- أتمتة أكبر قدر ممكن من الشغل.
- استخدام الأدوات والخطط المجانية قدر الإمكان.
- الشغل كله من الآيباد، من خلال Claude Code على الويب.

## Decisions so far / القرارات حتى الآن

| Item / البند | Choice / الاختيار |
|---|---|
| Video language / لغة الفيديو | Egyptian Arabic (عامية مصرية) |
| Platforms / المنصات | TikTok, Instagram Reels, YouTube Shorts |
| Format / الشكل | Vertical 9:16, under 60 seconds / رأسي 9:16، أقل من 60 ثانية |
| Style / الأسلوب | Faceless, no creator voice / من غير وجه ولا صوت صانع المحتوى |
| First topic / أول موضوع | First aid tips for the Christmas season (ميلاد المسيح): common holiday injuries / إسعافات أولية لموسم الأعياد: إصابات شائعة |

## Folder structure / هيكل المجلدات

| Folder / المجلد | What goes here / المحتوى |
|---|---|
| `scripts/` | Automation scripts (added later, once the workflow is tested) / سكريبتات الأتمتة (لاحقاً بعد تجربة الشغل) |
| `prompts/` | Reusable prompts for AI video, voice, and image tools / أوامر جاهزة لأدوات الفيديو والصوت والصور |
| `videos/` | Finished videos and their notes (large files may be kept elsewhere) / الفيديوهات الجاهزة وملاحظاتها |
| `assets/` | Logos, caption styles, background music, and other reusable files / شعارات وأنماط كتابة ومقاطع موسيقى وملفات متكررة |

## Content rules / قواعد المحتوى

1. **Educational only.** Videos explain concepts and cases in general terms. They are not medical advice.
   **تعليمي فقط.** الفيديو بيشرح مفاهيم بشكل عام، ومش بديل عن استشارة الطبيب.
2. **No real patient information.** Never use real names, images, records, or identifiable details. Cases must be fully anonymized and changed.
   **مفيش بيانات مريض حقيقية.** ممنوع أسماء أو صور أو ملفات أو تفاصيل تعرّف بالمريض. الحالات لازم تتغير بالكامل.
3. **Use reliable sources.** Base every claim on textbooks, guidelines, or peer-reviewed sources. Note the source for each video.
   **مصادر موثوقة.** كل معلومة لازم تكون من كتاب أو إرشادات معتمدة أو بحث محكّم، وتتسجل المصدر لكل فيديو.
4. **Disclaimer on every video.** Example: "For educational purposes only. Not medical advice. Consult a doctor for medical concerns."
   **تنويه في كل فيديو.** مثال: "للتثقيف الصحي فقط، وليس نصيحة طبية. استشر طبيبك في أي مشكلة صحية."
5. **Label AI-generated content.** Use the platform's AI-content label whenever footage looks realistic.
   **علّم المحتوى المولّد بالذكاء الاصطناعي.** استخدم علامة المنصة لأي مقطع يبدو واقعي.
6. **No impersonation.** Do not use the names, logos, or likeness of real doctors, hospitals, or organizations.
   **ممنوع الانتحال.** ما تستخدمش أسماء أو شعارات أو شبه أطباء أو مستشفيات أو جهات حقيقية.

## Basic workflow (planned) / خطوات الشغل (مخططة)

1. Choose a topic and write a short script (hook, explanation, key lesson, disclaimer).
2. Generate narration with a text-to-speech tool (Egyptian Arabic).
3. Generate or select visuals with AI video and image tools.
4. Add captions (Arabic) and a thumbnail.
5. Export as 9:16, check it is under 60 seconds, and save the notes in `videos/`.

1. اختيار الموضوع وكتابة سكريبت قصير (جملة افتتاحية، شرح، الدرس الأساسي، تنويه).
2. توليد التعليق الصوتي بأداة تحويل النص لصوت (عامية مصرية).
3. توليد أو اختيار اللقطات بأدوات الفيديو والصور بالذكاء الاصطناعي.
4. إضافة الكابشن بالعربي وصورة مصغرة.
5. التصدير بنسبة 9:16، والتأكد إنه أقل من 60 ثانية، وحفظ الملاحظات في `videos/`.

Each step will be documented here as it is tested. / كل خطوة هتتوثق هنا بعد ما تتجرب.

## Status / الحالة

Setup stage. Language, platforms, and first topic are chosen. Tools and the first test video are still to be chosen.

مرحلة الإعداد. تم تحديد اللغة والمنصات وأول موضوع. الأدوات وأول فيديو تجريبي لسه هيتحددوا.
