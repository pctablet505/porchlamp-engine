// first-pull-requests-page.js — the one script of /first-pull-requests/ (views/first_pull_requests.js): the slider over the
// waiting curve and the charts' hover text. The page reads whole without it: the opening reading and every table stand.

const curve = document.querySelector("[data-fpr-curve]");
if (curve) {
  const list = (name) => curve.dataset[name].split("|");
  const [labels, first, returning, xs, said] = [list("labels"), list("first"), list("returning"), list("x"), list("said")];
  const slide = curve.querySelector("[data-fpr-slide]");
  const input = slide.querySelector("input");
  const chart = curve.nextElementSibling;
  const guide = chart.querySelector("[data-fpr-guide]");
  const dots = [...chart.querySelectorAll("[data-fpr-i]")];
  const show = (i) => {
    slide.querySelector("[data-fpr-label]").textContent = labels[i];
    input.setAttribute("aria-valuetext", said[i]);
    curve.querySelector("[data-fpr-first]").textContent = first[i];
    curve.querySelector("[data-fpr-returning]").textContent = returning[i];
    guide.style.left = xs[i];
    for (const dot of dots) dot.classList.toggle("fpr-dot-picked", Number(dot.dataset.fprI) === i);
  };
  slide.hidden = false;
  curve.querySelector("[data-fpr-silent-for]").hidden = true;
  input.addEventListener("input", () => show(Number(input.value)));
  show(Number(input.value));
}

const tip = document.getElementById("fpr-tip");
if (tip) {
  const place = (event) => {
    const target = event.target instanceof Element ? event.target.closest("[data-tip]") : null;
    tip.hidden = !target;
    if (!target) return;
    tip.textContent = target.getAttribute("data-tip");
    const left = Math.max(8, Math.min(event.clientX + 12, innerWidth - tip.offsetWidth - 8));
    tip.style.transform = `translate(${left}px, ${Math.max(8, event.clientY - tip.offsetHeight - 12)}px)`;
  };
  document.addEventListener("pointermove", place);
  document.addEventListener("pointerdown", place);
  addEventListener("scroll", () => { tip.hidden = true; }, { passive: true, capture: true });
}
