import {getDocument, GlobalWorkerOptions} from './vendor/pdf.min.mjs';

GlobalWorkerOptions.workerSrc = '/static/vendor/pdf.worker.min.mjs';
let documentTask = null, pdf = null, renderTask = null, renderVersion = 0;

export function clearPDF() {
  renderVersion += 1;
  renderTask?.cancel();
  renderTask = null;
  documentTask?.destroy();
  documentTask = null;
  pdf = null;
}

export async function loadPDF(blob) {
  clearPDF();
  const version = renderVersion;
  documentTask = getDocument({data: new Uint8Array(await blob.arrayBuffer())});
  const loaded = await documentTask.promise;
  if (version !== renderVersion) return 0;
  pdf = loaded;
  return loaded.numPages;
}

export async function renderPDF(pageNumber, zoom) {
  if (!pdf) return;
  const version = ++renderVersion;
  if (renderTask) { renderTask.cancel(); try { await renderTask.promise; } catch {} }
  const page = await pdf.getPage(Math.max(1, Math.min(pageNumber, pdf.numPages)));
  if (version !== renderVersion) return;
  const container = document.querySelector('#pdf-preview');
  const canvas = document.querySelector('#pdf-canvas');
  const base = page.getViewport({scale: 1});
  const scale = zoom === 'page-width' ? (container.clientWidth - 48) / base.width : Number(zoom) / 100 * 96 / 72;
  const viewport = page.getViewport({scale});
  const resolution = window.devicePixelRatio || 1;
  canvas.width = Math.floor(viewport.width * resolution);
  canvas.height = Math.floor(viewport.height * resolution);
  canvas.style.width = Math.floor(viewport.width) + 'px';
  canvas.style.height = Math.floor(viewport.height) + 'px';
  canvas.setAttribute('aria-label', `Расписание, страница ${pageNumber} из ${pdf.numPages}`);
  renderTask = page.render({canvasContext: canvas.getContext('2d'), viewport,
                            transform: resolution === 1 ? null : [resolution, 0, 0, resolution, 0, 0]});
  try { await renderTask.promise; }
  catch (error) { if (error.name !== 'RenderingCancelledException') throw error; }
}
