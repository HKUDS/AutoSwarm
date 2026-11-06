// Desk's own interface text: Chinese is the source, English the default display.
// What agents write (prompts, replies, tool calls, native titles) is always shown as written.
import EN from './locale-en.json' with { type: 'json' };

let current = 'en';
export const LANGUAGES = [['en', 'English'], ['zh', '中文']];
export const language = () => current;
export const locale = () => current === 'zh' ? 'zh-CN' : 'en-GB';  // en-GB: 24-hour times, as compact as zh-CN
export function setLanguage(value) {
  current = value === 'zh' ? 'zh' : 'en';
  if (typeof document !== 'undefined') document.documentElement.lang = locale();
  return current;
}
/**
 * t('已等 {time}', { time }) with the Chinese source text as the key. An English
 * entry may be [singular, plural], chosen by values.count.
 */
export function t(text, values) {
  let out = current === 'en' && Object.hasOwn(EN, text) ? EN[text] : text;
  if (Array.isArray(out)) out = out[values?.count === 1 ? 0 : 1];
  return values ? out.replace(/\{(\w+)\}/g, (all, key) => Object.hasOwn(values, key) ? String(values[key]) : all) : out;
}
