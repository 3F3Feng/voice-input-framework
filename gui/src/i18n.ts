// 界面语言(F22):中文 / 英文。
//
// 只有两种语言,所以不搞键值表:每处文案就地写成 t("中文", "English"),
// 两种说法摆在一起,改一边不会忘了另一边。`locale` 是响应式的,模板里用 t()
// 的地方切换语言后自动重渲染。
//
// 「跟随系统」在这里解析(WebView 的 navigator.languages 跟着系统语言走),
// 解析结果告诉 Rust(`set_ui_language`),托盘和 Rust 发出的提示跟着换。

import { ref } from "vue";
import { invoke } from "@tauri-apps/api/core";

export type LanguagePref = "auto" | "zh" | "en";
export type Locale = "zh" | "en";

export const locale = ref<Locale>("zh");

/** 系统首选语言是中文就用中文,否则英文。 */
export function systemLocale(): Locale {
  const langs = navigator.languages?.length ? navigator.languages : [navigator.language];
  const first = (langs[0] || "zh").toLowerCase();
  return first.startsWith("zh") ? "zh" : "en";
}

export function resolveLocale(pref: string | undefined): Locale {
  return pref === "zh" || pref === "en" ? pref : systemLocale();
}

/** 按偏好切换界面语言,并同步给 Rust。 */
export function applyLanguagePref(pref: string | undefined) {
  locale.value = resolveLocale(pref);
  document.documentElement.lang = locale.value === "zh" ? "zh-CN" : "en";
  invoke("set_ui_language", { lang: locale.value }).catch(() => {});
}

/** 按当前界面语言挑一句。英文里要插值就用模板字符串:t(`还剩 ${n} 秒`, `${n}s left`)。 */
export function t(zh: string, en: string): string {
  return locale.value === "en" ? en : zh;
}
