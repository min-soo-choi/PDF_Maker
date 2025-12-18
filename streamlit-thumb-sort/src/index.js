import { Streamlit } from "streamlit-component-lib";
import Sortable from "sortablejs";

let sortable = null;
let currentItems = [];
let currentIndex = 0;
let lastSentOrder = null;

const styles = `
  :root { color-scheme: light; }
  body { margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  .wrapper { padding: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); gap: 16px; }
  .card { border: 1px solid #ddd; border-radius: 12px; overflow: hidden; background: #fff; box-shadow: 0 3px 8px rgba(0,0,0,0.08); cursor: grab; }
  .card:active { cursor: grabbing; }
  .thumb { width: 100%; aspect-ratio: 3 / 4; background-size: contain; background-repeat: no-repeat; background-position: center; background-color: #f7f7f7; }
  .label { padding: 8px 10px; display: flex; align-items: center; gap: 6px; font-size: 13px; color: #333; }
  .badge { display: inline-flex; align-items: center; justify-content: center; min-width: 22px; height: 22px; padding: 0 6px; background: #f0f2f5; color: #111; border-radius: 999px; font-size: 12px; }
  .overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: none; align-items: center; justify-content: center; z-index: 9999; }
  .overlay img { max-width: 98vw; max-height: 94vh; border-radius: 8px; box-shadow: 0 8px 32px rgba(0,0,0,0.35); }
  .nav-btn { position: absolute; top: 50%; transform: translateY(-50%); background: rgba(0,0,0,0.55); color: #fff; border: none; border-radius: 999px; width: 42px; height: 42px; cursor: pointer; font-size: 18px; display: flex; align-items: center; justify-content: center; }
  .nav-btn:hover { background: rgba(0,0,0,0.7); }
  .nav-left { left: 16px; }
  .nav-right { right: 16px; }
  .indicator { position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.65); color: #fff; padding: 6px 10px; border-radius: 999px; font-size: 12px; }
`;

function updateBadges(grid) {
  const cards = Array.from(grid.children || []);
  cards.forEach((card, idx) => {
    const badge = card.querySelector(".badge");
    if (badge) badge.textContent = `${idx + 1}`;
  });
}

function sendOrderToStreamlit(grid) {
  const order = Array.from(grid.children || []).map((el) => el.dataset.id);
  const joined = order.join("|");
  if (joined !== lastSentOrder) {
    lastSentOrder = joined;
    Streamlit.setComponentValue(order);
  }
}

function render(event) {
  const { items = [] } = event.detail.args;
  currentItems = items;
  const root = document.getElementById("root");
  if (!root) return;

  root.innerHTML = `
    <style>${styles}</style>
    <div class="wrapper">
      <div id="thumb-grid" class="grid">
        ${items
          .map(
            (item, idx) => `
              <div class="card" data-id="${item.id}" data-full="${item.full || item.thumb}">
                <div class="thumb" style="background-image: url('${item.thumb}')"></div>
                <div class="label">
                  <span class="badge">${idx + 1}</span>
                  <span title="${item.label}">${item.label}</span>
                </div>
              </div>
            `
          )
          .join("")}
      </div>
    </div>
    <div id="overlay" class="overlay">
      <button class="nav-btn nav-left" id="nav-left">&#9664;</button>
      <img id="overlay-img" src="" alt="preview" />
      <button class="nav-btn nav-right" id="nav-right">&#9654;</button>
      <div class="indicator" id="indicator"></div>
    </div>
  `;

  const grid = document.getElementById("thumb-grid");
  if (!grid) return;

  if (sortable) {
    sortable.destroy();
  }
  sortable = Sortable.create(grid, {
    animation: 0,
    dataIdAttr: "data-id",
    delay: 50,
    delayOnTouchOnly: true,
    forceFallback: true,
    fallbackTolerance: 8,
    scroll: true,
    onEnd: () => {
      const order = Array.from(grid.children).map((el) => el.dataset.id);
      // reorder local items to prevent flicker before rerender
      currentItems = order
        .map((id) => currentItems.find((it) => it.id === id))
        .filter(Boolean);
      lastSentOrder = order.join("|");
      Streamlit.setComponentValue(order);
      updateBadges(grid);
    },
  });

  updateBadges(grid);
  sendOrderToStreamlit(grid);

  const overlay = document.getElementById("overlay");
  const overlayImg = document.getElementById("overlay-img");
  const navLeft = document.getElementById("nav-left");
  const navRight = document.getElementById("nav-right");
  const indicator = document.getElementById("indicator");

  const showImage = (idx) => {
    if (!currentItems.length) return;
    if (idx < 0) idx = 0;
    if (idx >= currentItems.length) idx = currentItems.length - 1;
    currentIndex = idx;
    const item = currentItems[currentIndex];
    overlayImg.src = item.full || item.thumb;
    overlay.style.display = "flex";
    if (indicator) {
      indicator.textContent = `${currentIndex + 1} / ${currentItems.length}`;
    }
    if (navLeft) {
      navLeft.style.display = currentIndex === 0 ? "none" : "flex";
    }
    if (navRight) {
      navRight.style.display = currentIndex === currentItems.length - 1 ? "none" : "flex";
    }
  };

  grid.addEventListener("click", (e) => {
    const card = e.target.closest(".card");
    if (!card) return;
    const id = card.dataset.id;
    const idx = currentItems.findIndex((it) => it.id === id);
    if (idx === -1) return;
    showImage(idx);
  });

  overlay.addEventListener("click", () => {
    overlay.style.display = "none";
    overlayImg.src = "";
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
  });

  if (navLeft) {
    navLeft.addEventListener("click", (ev) => {
      ev.stopPropagation();
      showImage(currentIndex - 1);
    });
  }

  if (navRight) {
    navRight.addEventListener("click", (ev) => {
      ev.stopPropagation();
      showImage(currentIndex + 1);
    });
  }

  Streamlit.setFrameHeight(document.body.scrollHeight);
}

Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, render);
Streamlit.setComponentReady();
Streamlit.setFrameHeight(0);
