'use strict';

const {pieceFor} = require('./user-identity.cjs');

const MARGIN = 20;
const HEADER_HEIGHT = 74;
const COLUMN_WIDTH = 194;
const COLUMN_GAP = 10;
const ROW_HEIGHT = 53;
const PREVIEW_WIDTH = 58;
const PREVIEW_HEIGHT = 38;

const COLORS = {
  B: '#775224',
  I: '#245b88',
  M: '#654589',
  T: '#9c2354',
};

function drawNumberedReview({artwork, layout, parts, identities, createCanvas}) {
  // Reuse the rendered component bounds. The review drawing does not change them.
  const piece = pieceFor(layout, false);
  const rowsInArtwork = Math.max(8, Math.ceil(artwork.height / ROW_HEIGHT));
  const columns = Math.min(4, Math.ceil(layout.elements.length / rowsInArtwork));
  const rowCount = Math.ceil(layout.elements.length / columns);
  const panelWidth = columns * COLUMN_WIDTH + (columns - 1) * COLUMN_GAP;
  const artworkBounds = {x: MARGIN, y: HEADER_HEIGHT, width: artwork.width, height: artwork.height};
  const panelX = artworkBounds.x + artworkBounds.width + MARGIN;
  const boardWidth = Math.max(370, panelX + panelWidth + MARGIN);
  const boardHeight = HEADER_HEIGHT + Math.max(artwork.height, rowCount * ROW_HEIGHT) + MARGIN;
  const canvas = createCanvas(boardWidth, boardHeight);
  const context = canvas.getContext('2d');

  context.fillStyle = '#eef1f5';
  context.fillRect(0, 0, boardWidth, HEADER_HEIGHT);
  context.fillRect(0, HEADER_HEIGHT, artworkBounds.x, artworkBounds.height);
  const rightEdge = artworkBounds.x + artworkBounds.width;
  context.fillRect(rightEdge, HEADER_HEIGHT, boardWidth - rightEdge, artworkBounds.height);
  const bottomEdge = artworkBounds.y + artworkBounds.height;
  context.fillRect(0, bottomEdge, boardWidth, boardHeight - bottomEdge);
  context.drawImage(artwork, artworkBounds.x, artworkBounds.y);

  context.fillStyle = '#192c43';
  context.textBaseline = 'alphabetic';
  context.font = '700 28px "Microsoft YaHei", Arial, sans-serif';
  context.fillText(`\u4f5c\u54c1 ${piece}`, MARGIN, 47);
  context.font = '600 16px "Microsoft YaHei", Arial, sans-serif';
  context.fillText('\u5143\u7d20\u5b9a\u4f4d', Math.max(panelX, 168), 45);

  // Scale the full artwork once, then highlight one element per miniature.
  const miniature = createCanvas(PREVIEW_WIDTH, PREVIEW_HEIGHT);
  const miniContext = miniature.getContext('2d');
  const scale = Math.min((PREVIEW_WIDTH - 4) / artwork.width, (PREVIEW_HEIGHT - 4) / artwork.height);
  const miniWidth = artwork.width * scale;
  const miniHeight = artwork.height * scale;
  const miniLeft = (PREVIEW_WIDTH - miniWidth) / 2;
  const miniTop = (PREVIEW_HEIGHT - miniHeight) / 2;
  miniContext.fillStyle = '#e1e7ee';
  miniContext.fillRect(0, 0, PREVIEW_WIDTH, PREVIEW_HEIGHT);
  miniContext.globalAlpha = 0.62;
  miniContext.drawImage(artwork, miniLeft, miniTop, miniWidth, miniHeight);

  const entries = identities.map((identity, i) => {
    const element = layout.elements[i];
    const component = parts[i].record;
    const column = Math.floor(i / rowCount);
    const row = i % rowCount;
    const x = panelX + column * (COLUMN_WIDTH + COLUMN_GAP);
    const y = HEADER_HEIGHT + row * ROW_HEIGHT;
    const locator = {x: x + 7, y: y + 4, width: PREVIEW_WIDTH, height: PREVIEW_HEIGHT};
    const label = {x: x + 73, y: y + 3, width: COLUMN_WIDTH - 80, height: 40};
    const color = COLORS[identity.short_id[0]] || '#304a67';

    context.fillStyle = '#fff';
    context.fillRect(x, y, COLUMN_WIDTH, 46);
    context.drawImage(miniature, locator.x, locator.y);

    const left = Math.max(0, Math.min(artwork.width, component.x));
    const right = Math.max(0, Math.min(artwork.width, component.x + component.width));
    const top = Math.max(0, Math.min(artwork.height, component.y));
    const bottom = Math.max(0, Math.min(artwork.height, component.y + component.height));
    const markerX = locator.x + miniLeft + left * scale;
    const markerY = locator.y + miniTop + top * scale;
    const markerWidth = (right - left) * scale;
    const markerHeight = (bottom - top) * scale;
    if (markerWidth < 3 || markerHeight < 3) {
      context.fillStyle = color;
      context.beginPath();
      context.arc(markerX + markerWidth / 2, markerY + markerHeight / 2, 2, 0, 2 * Math.PI);
      context.fill();
    } else {
      context.strokeStyle = color;
      context.lineWidth = 1.5;
      context.strokeRect(markerX, markerY, markerWidth, markerHeight);
    }

    const category = identity.category ?? (element.type === 'text' ? '\u6587\u672c' : element.role === 'background' ? '\u80cc\u666f' : element.type === 'image' ? '\u56fe\u7247' : '\u6a21\u5757');
    context.fillStyle = color;
    context.font = '700 21px Arial, sans-serif';
    context.fillText(identity.short_id, label.x, label.y + 18, label.width);
    context.fillStyle = '#40516a';
    context.font = '13px "Microsoft YaHei", Arial, sans-serif';
    context.fillText(category, label.x, label.y + 35, label.width);

    return {
      internalId: identity.internal_id,
      shortId: identity.short_id,
      qualifiedReference: identity.qualified_id,
      category: identity.category,
      label,
      locator,
    };
  });

  return {canvas, index: {piece, artwork: artworkBounds, entries}};
}

module.exports = {drawNumberedReview};
