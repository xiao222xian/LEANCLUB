import type { DesignVariant } from "../engine/types";

const escape = (value: string) =>
  value.replace(
    /[<>&"']/g,
    (char) =>
      ({
        "<": "&lt;",
        ">": "&gt;",
        "&": "&amp;",
        '"': "&quot;",
        "'": "&apos;",
      })[char]!,
  );
export function svgDocument(
  variant: DesignVariant,
  color: string,
  background?: string,
  width = 2048,
): string {
  if (
    !/^#[\da-f]{6}$/i.test(color) ||
    (background && !/^#[\da-f]{6}$/i.test(background))
  )
    throw new Error("颜色格式无效");
  if (
    !variant.paths.length ||
    variant.paths.some((path) => /NaN|Infinity|[<>"&]/.test(path))
  )
    throw new Error("轮廓无效，请重新生成");
  const [x, y, w, h] = variant.viewBox;
  const height = Math.round((width * h) / w);
  return `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${variant.viewBox.join(" ")}"><title>${escape(variant.text)} · ${escape(variant.name)}</title>${background ? `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${background}"/>` : ""}<g fill="${color}" fill-rule="${variant.fillRule}">${variant.paths.map((d) => `<path d="${d}"/>`).join("")}</g></svg>`;
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 15000);
}

export async function exportArtwork(
  variant: DesignVariant,
  options: {
    format: "svg" | "png";
    color: string;
    background?: string;
    width: number;
  },
) {
  const svg = svgDocument(
    variant,
    options.color,
    options.background,
    options.width,
  );
  const filename = `${variant.text}-${variant.id}`;
  const blob = new Blob([svg], { type: "image/svg+xml;charset=utf-8" });
  if (options.format === "svg") {
    saveBlob(blob, `${filename}.svg`);
    return;
  }
  const url = URL.createObjectURL(blob);
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("图片转换失败，请重试或导出 SVG"));
      image.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = options.width;
    canvas.height = Math.round(
      (options.width * variant.viewBox[3]) / variant.viewBox[2],
    );
    const context = canvas.getContext("2d");
    if (!context) throw new Error("当前浏览器无法导出 PNG，请导出 SVG");
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    const png = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (result) =>
          result ? resolve(result) : reject(new Error("PNG 导出失败，请重试")),
        "image/png",
      ),
    );
    saveBlob(png, `${filename}.png`);
  } finally {
    URL.revokeObjectURL(url);
  }
}
