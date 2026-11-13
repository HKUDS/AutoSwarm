// Main-process side of renderer/i18n.mjs: the same dictionary, the language from Desk's preferences.
const EN = require('../renderer/locale-en.json');

let current = 'en';
exports.language = () => current;
exports.setLanguage = value => (current = value === 'zh' ? 'zh' : 'en');
exports.t = (text, values) => {
  let out = current === 'en' && Object.hasOwn(EN, text) ? EN[text] : text;
  if (Array.isArray(out)) out = out[values?.count === 1 ? 0 : 1];
  return values ? out.replace(/\{(\w+)\}/g, (all, key) => Object.hasOwn(values, key) ? String(values[key]) : all) : out;
};
