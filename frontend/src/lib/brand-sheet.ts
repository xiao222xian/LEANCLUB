import type { DesignVariant } from '../engine/types';
import { svgDocument } from './export';

export type BrandScene = 'card' | 'poster';
export const BRAND_PHOTO_URL = '/images/hero-1024-v2.webp';
const sizes = { card: [1200, 700], poster: [900, 1200] } as const;
const embeddedPhoto = /^data:image\/(?:webp|png|jpeg);base64,[A-Za-z\d+/]+={0,2}$/;

/** The same exported logo paths are nested without flattening, redrawing, or fonts. */
function logoAt(variant: DesignVariant, color: string, x: number, y: number, width: number, height: number) {
  return svgDocument(variant, color).replace(
    /^<\?xml[^>]*>\s*<svg[^>]*>/,
    `<svg x="${x}" y="${y}" width="${width}" height="${height}" viewBox="${variant.viewBox.join(' ')}" preserveAspectRatio="xMidYMid meet">`,
  );
}

export function brandSheetDocument(variant: DesignVariant, options: {
  scene: BrandScene; color: string; background: string; photoDataUrl?: string;
}): { svg: string; width: number; height: number } {
  if (!/^#[\da-f]{6}$/i.test(options.color) || !/^#[\da-f]{6}$/i.test(options.background)) throw new Error('颜色格式无效');
  if (!Object.hasOwn(sizes, options.scene)) throw new Error('样张类型无效');
  if (options.scene === 'poster' && !embeddedPhoto.test(options.photoDataUrl ?? '')) throw new Error('样张照片尚未加载，请重试');
  const [width, height] = sizes[options.scene];
  const color = options.color, background = options.background;
  const font = 'Arial, PingFang SC, Microsoft YaHei, sans-serif';
  const composition = options.scene === 'card'
    ? `<rect width="1200" height="700" fill="${background}"/>
<g fill="${color}" font-family="${font}">
<text x="70" y="78" font-size="21" letter-spacing="2.5">LEAN SCHOOL</text>
<text x="1130" y="78" font-size="19" text-anchor="end">薄肌学院</text>
</g>
${logoAt(variant, color, 76, 184, 1048, 286)}
<path d="M70 568H1130" stroke="${color}" stroke-opacity=".25"/>
<g fill="${color}" font-family="${font}">
<text x="70" y="629" font-size="26" letter-spacing="2">多运动。多思考。</text>
<text x="1130" y="629" font-size="16" letter-spacing="2" text-anchor="end" opacity=".62">品牌名片 · 应用样张</text>
</g>`
    : `<rect width="900" height="1200" fill="${background}"/>
<image x="0" y="0" width="900" height="736" href="${options.photoDataUrl}" preserveAspectRatio="xMidYMid slice"/>
<rect x="40" y="40" width="820" height="164" fill="${background}"/>
${logoAt(variant, color, 72, 68, 756, 108)}
<g fill="${color}" font-family="${font}">
<text x="58" y="884" font-size="114" font-weight="700" letter-spacing="-3">多运动。</text>
<text x="58" y="1014" font-size="114" font-weight="700" letter-spacing="-3">多思考。</text>
<text x="62" y="1132" font-size="21" letter-spacing="2">薄肌学院</text>
<text x="838" y="1132" font-size="17" text-anchor="end" opacity=".64">活动海报 · 应用样张</text>
</g>`;
  return {
    width, height,
    svg: `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><title>${options.scene === 'card' ? '品牌名片' : '薄肌活动海报'} · 应用样张</title>${composition}</svg>`,
  };
}

/** Only the site's existing photo is fetched; downloads contain embedded bytes. */
export async function loadBrandPhoto(fetcher: typeof fetch = fetch): Promise<string> {
  const response = await fetcher(BRAND_PHOTO_URL, { cache: 'force-cache' });
  if (!response.ok) throw new Error('样张照片加载失败，请重试');
  const photo = await response.blob();
  if (!/^image\/(?:webp|png|jpeg)$/.test(photo.type)) throw new Error('样张照片格式无效，请重试');
  const bytes = new Uint8Array(await photo.arrayBuffer());
  if (!bytes.length) throw new Error('样张照片为空，请重试');
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return `data:${photo.type};base64,${btoa(binary)}`;
}

function saveSheetBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 15000);
}

export async function exportBrandSheet(variant: DesignVariant, options: {
  scene: BrandScene; color: string; background: string; photoDataUrl?: string; format: 'svg' | 'png';
}) {
  const sheet = brandSheetDocument(variant, options);
  const blob = new Blob([sheet.svg], { type: 'image/svg+xml;charset=utf-8' });
  const filename = `${variant.text}-${variant.id}-${options.scene}-sample`;
  if (options.format === 'svg') { saveSheetBlob(blob, `${filename}.svg`); return; }
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error('样张转换失败，请重试或下载 SVG'));
      image.src = url;
    });
    const canvas = document.createElement('canvas');
    canvas.width = sheet.width * 2; canvas.height = sheet.height * 2;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('当前浏览器无法导出 PNG，请下载 SVG');
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const png = await new Promise<Blob>((resolve, reject) => canvas.toBlob(result => result ? resolve(result) : reject(new Error('PNG 导出失败，请重试')), 'image/png'));
    saveSheetBlob(png, `${filename}.png`);
  } finally { URL.revokeObjectURL(url); }
}
