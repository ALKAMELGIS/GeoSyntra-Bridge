/**
 * Interface language for the app (the site keeps its own copy in site/i18n.ts; both share the stored choice).
 * `tr("English text")` returns the Arabic when Arabic is on and a translation exists, else the English.
 * `{n}`-style placeholders are filled from `vars`. Layer, field and feature-class names are data and never pass here.
 */
import { useEffect, useState } from "react";
import { getLang, saveLang, type Lang } from "./site/i18n";

let cur: Lang = getLang();
const subs = new Set<() => void>();

export const lang = () => cur;
export const isAr = () => cur === "ar";

export function applyDir() {
  try {
    document.documentElement.lang = cur;
    document.documentElement.dir = cur === "ar" ? "rtl" : "ltr";
  } catch { /* no DOM */ }
}
applyDir();

export function setLang(l: Lang) {
  cur = l; saveLang(l); applyDir();
  subs.forEach((f) => f());
}
/** Re-renders the caller when the language changes. */
export function useLang(): Lang {
  const [, bump] = useState(0);
  useEffect(() => { const f = () => bump((x) => x + 1); subs.add(f); return () => { subs.delete(f); }; }, []);
  return cur;
}

export function tr(en: string, vars?: Record<string, string | number>): string {
  let s = cur === "ar" ? (AR[en] ?? en) : en;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(typeof v === "number" ? v.toLocaleString("en") : v);
  return s;
}

// GIS terms used throughout: طبقة (layer) · فئة معالم (feature class) · قاعدة بيانات (geodatabase) · التقاط (snapping)
const AR: Record<string, string> = {
  // shell
  "Conversions": "التحويلات", "Account and billing": "الحساب والفوترة", "Owner console": "لوحة المالك",
  "New conversion": "تحويل جديد", "Upload": "الرفع", "Layers": "الطبقات", "Review": "المراجعة", "Deliver": "التسليم",
  "Next": "التالي", "Checking…": "جارٍ الفحص…", "working…": "جارٍ العمل…", "Language": "اللغة",
  "Checking the data against the template — Review opens when it is done": "جارٍ فحص البيانات على القالب، وتُفتح المراجعة عند الانتهاء",
  "Upload a drawing first": "ارفع مخططًا أولًا", "Connect the layers first": "اربط الطبقات أولًا",
  "Connections": "الروابط", "Workspace": "مساحة العمل", "List": "قائمة",
  // workspace
  "Search drawings": "ابحث في المخططات", "Delivered this month": "سُلّم هذا الشهر", "Waiting on you": "بانتظارك",
  "Features delivered": "العناصر المسلّمة", "Credits": "الرصيد", "In progress": "قيد العمل", "Unlimited": "غير محدود",
  "No deliveries yet": "لا تسليمات بعد", "Nothing to fix": "لا شيء للإصلاح", "Open the list on the right": "افتح القائمة على اليمين",
  "Top up or change plan": "اشحن الرصيد أو غيّر الخطة", "Mapping or working": "مطابقة أو معالجة",
  "{n} vs last month": "{n} مقارنة بالشهر الماضي", "in {n} geodatabases": "في {n} قاعدة بيانات", "in 1 geodatabase": "في قاعدة بيانات واحدة",
  "All": "الكل", "Needs you": "يحتاجك", "Delivered": "مُسلّم", "Files are deleted after 72 hours": "تُحذف الملفات بعد 72 ساعة",
  "Drawing": "المخطط", "Progress": "التقدم", "Status": "الحالة", "Features": "العناصر", "Updated": "آخر تحديث",
  "Needs attention": "يحتاج تدخلًا", "{n} to fix": "{n} للإصلاح", "Ready to deliver": "جاهز للتسليم", "Working": "جارٍ العمل",
  "Mapping layers": "مطابقة الطبقات", "Loading…": "جارٍ التحميل…", "Nothing here": "لا شيء هنا", "No drawing matches “{q}”": "لا مخطط يطابق «{q}»",
  "Nothing waiting. Every open drawing is moving.": "لا شيء بانتظارك. كل المخططات المفتوحة تتقدم.",
  "{n} features fail the check": "{n} عنصرًا لا يجتاز الفحص", "Stopped — open to see why": "توقف — افتحه لمعرفة السبب",
  "selected": "محدد", "Clear": "مسح", "Delete": "حذف", "Open": "فتح", "Keep": "إبقاء", "Deleting…": "جارٍ الحذف…",
  "Your first conversion takes a few minutes": "أول تحويل يستغرق بضع دقائق",
  "Drop a DWG or DXF, confirm the layers, fix what the check flags, download the geodatabase.": "أفلت ملف DWG أو DXF، أكّد الطبقات، أصلح ما يظهره الفحص، ونزّل قاعدة البيانات.",
  "Start a conversion": "ابدأ تحويلًا", "just now": "الآن", "Yesterday": "أمس", "{n} min ago": "قبل {n} دقيقة", "{n} h ago": "قبل {n} ساعة",
  "The drawing, its review edits and its packages are removed for good. This cannot be undone.": "يُحذف المخطط وتعديلات مراجعته وحزمه نهائيًا. لا يمكن التراجع عن ذلك.",
  "Delete {name}": "حذف {name}", "Delete {n} drawings": "حذف {n} مخططات",
  // review ribbon
  "Map": "الخريطة", "Edit": "تحرير", "Navigate": "التنقل", "Explore": "استكشاف", "Zoom in": "تكبير", "Zoom out": "تصغير",
  "Full extent": "الرسم كاملًا", "Zoom to selection": "تكبير إلى المحدد", "Layer": "الطبقة", "Basemap": "الخريطة الأساسية",
  "Selection": "التحديد", "Select": "تحديد", "Select by box": "تحديد بمربع", "Inquiry": "الاستعلام", "Distance": "المسافة", "Area": "المساحة",
  "Manage edits": "إدارة التعديلات", "Save": "حفظ", "Discard": "تجاهل", "Undo": "تراجع", "Redo": "إعادة",
  "Snapping": "الالتقاط", "Snap off": "الالتقاط متوقف", "Snap options": "خيارات الالتقاط", "Create": "إنشاء", "Modify": "تعديل",
  "Tools": "الأدوات", "Move": "تحريك", "Split": "تقسيم", "Merge": "دمج", "Explode": "تفكيك", "Flip": "عكس الاتجاه",
  "Attributes": "السمات", "Issues": "المشاكل", "Errors": "أخطاء", "To fill": "للتعبئة", "Clean": "سليم", "Fix all": "إصلاح الكل", "None": "لا شيء",
  "All layers": "كل الطبقات", "unsaved": "غير محفوظ", "Snap on": "الالتقاط مفعّل", "Snap off ": "الالتقاط متوقف",
  "features here": "عناصر هنا", "Select all": "تحديد الكل", "Vertices and ends": "الرؤوس والأطراف", "Edges": "الحواف", "Tolerance": "التسامح",
  "Pick a feature on the map": "اختر عنصرًا على الخريطة",
  "Its attributes open here. Select several to fill them together.": "تُفتح سماته هنا. حدّد عدة عناصر لتعبئتها معًا.",
  "Go to the next one to fill": "انتقل إلى التالي للتعبئة",
  // layers board
  "In the drawing": "في المخطط", "In the geodatabases": "في قواعد البيانات", "Filter layers": "تصفية الطبقات", "Set aside": "استبعاد",
  "Apply matches": "تطبيق المطابقات", "connected": "مربوط", "set aside": "مستبعد", "Connect to any feature class…": "اربط بأي فئة معالم…",
  "Select a layer on the left to connect it.": "اختر طبقة من القائمة لربطها.",
  "{a} layers · {b} objects": "{a} طبقة · {b} عنصر", "Points": "النقاط", "Lines": "الخطوط", "Areas": "المساحات",
  "Show set-aside": "إظهار المستبعد", "Hide set-aside": "إخفاء المستبعد", "{a} set aside · {b} geodatabases": "{a} مستبعد · {b} قاعدة بيانات",
  "Click a layer, then a feature class · Shift or Ctrl+click picks several · click a feature class to pick its layers · Esc clears":
    "انقر طبقة ثم فئة معالم · Shift أو Ctrl+نقر لتحديد عدة طبقات · انقر فئة معالم لتحديد طبقاتها · Esc للإلغاء",
  "Checked · layers to review": "تم الفحص · طبقات للمراجعة", "Checked": "تم الفحص", "Generated": "تم التوليد", "Template changed": "تم تغيير القالب",
  "Template updated": "تم تحديث القالب", "All complete": "اكتمل الكل", "Copied": "نُسخ", "Edits discarded.": "تم تجاهل التعديلات.", "Fixing…": "جارٍ الإصلاح…",
  "Kept as is (exception).": "أُبقي كما هو (استثناء).", "Saved": "تم الحفظ", "Undone": "تم التراجع",
  // upload
  "Click to upload or drop files": "انقر للرفع أو أفلت الملفات", "Browse files": "تصفح الملفات", "Automatic": "تلقائي",
  "Clean-up": "التنظيف", "Projection": "الإسقاط", "Submission": "التسليم", "Change": "تغيير", "Fill in": "تعبئة", "Add sheet": "إضافة لوحة",
  "Blocked": "متوقف", "Exploding…": "جارٍ التفكيك…", "Explode blocks": "تفكيك البلوكات",
  // deliver
  "Generate": "توليد", "Generate again": "توليد مجددًا", "Download package": "تنزيل الحزمة", "Ready to generate": "جاهز للتوليد",
  "Validate first": "افحص أولًا", "Fix {n} first": "أصلح {n} أولًا", "Generate · {n} credits": "توليد · {n} نقطة",
  // clean-up dialog
  "Advanced clean-up": "التنظيف المتقدم", "Nothing changes in the drawing until you apply": "لا يتغير شيء في المخطط حتى تطبّق",
  "Approve all": "موافقة على الكل", "Keep all": "إبقاء الكل", "Keep as drawn": "إبقاء كما رُسم", "Close": "إغلاق",
  "Apply and re-read": "طبّق وأعد القراءة", "approved": "موافق عليه", "Satellite": "قمر صناعي", "would change": "سيتغير", "kept": "يبقى",
  "Remove": "حذف", "Snap end": "التقاط الطرف", "Close as area": "إغلاق كمساحة", "Repair": "إصلاح", "Check only": "فحص فقط",
};
