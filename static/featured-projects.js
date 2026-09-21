(() => {
  const slider = document.querySelector('[data-slider]');
  if (!slider) return;
  const slides = [...slider.querySelectorAll('.featured-slide')];
  const images = [...slider.querySelectorAll('.featured-picture img')];
  function fitImages() {
    images.forEach(img => {
      if (!img.naturalWidth || !img.naturalHeight) return;
      const frame = img.parentElement;
      const width = frame.clientWidth;
      const height = frame.clientHeight;
      const scale = Math.min(1, img.naturalWidth / width, img.naturalHeight / height);
      img.style.width = `${width * scale}px`;
      img.style.height = `${height * scale}px`;
    });
  }
  images.forEach(img => img.addEventListener('load', fitImages));
  window.addEventListener('resize', fitImages);
  fitImages();
  if (slides.length < 2) return;
  const track = slider.querySelector('.featured-track');
  const dots = [...slider.querySelectorAll('[data-dot]')];
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let index = 0, timer, startX = null;
  function show(next) {
    index = (next + slides.length) % slides.length;
    track.style.transform = `translateX(${index * 100}%)`;
    slides.forEach((slide, i) => {
      slide.setAttribute('aria-hidden', String(i !== index));
      slide.querySelectorAll('a').forEach(a => a.tabIndex = i === index ? 0 : -1);
    });
    dots.forEach((dot, i) => i === index ? dot.setAttribute('aria-current', 'true') : dot.removeAttribute('aria-current'));
  }
  function pause() { clearInterval(timer); timer = null; }
  function resume() { pause(); if (!reduced.matches && !slider.matches(':hover') && !slider.contains(document.activeElement) && !document.hidden) timer = setInterval(() => show(index + 1), 5500); }
  slider.querySelector('[data-prev]').addEventListener('click', () => { show(index - 1); pause(); setTimeout(resume, 9000); });
  slider.querySelector('[data-next]').addEventListener('click', () => { show(index + 1); pause(); setTimeout(resume, 9000); });
  dots.forEach((dot, i) => dot.addEventListener('click', () => { show(i); pause(); setTimeout(resume, 9000); }));
  slider.addEventListener('pointerenter', pause);
  slider.addEventListener('pointerleave', resume);
  slider.addEventListener('focusin', pause);
  slider.addEventListener('focusout', () => setTimeout(resume, 0));
  slider.addEventListener('touchstart', e => { startX = e.touches[0].clientX; pause(); }, {passive:true});
  slider.addEventListener('touchend', e => { if (startX !== null && Math.abs(e.changedTouches[0].clientX - startX) > 45) show(index + (e.changedTouches[0].clientX > startX ? 1 : -1)); startX = null; setTimeout(resume, 9000); }, {passive:true});
  document.addEventListener('visibilitychange', resume);
  reduced.addEventListener('change', resume);
  resume();
})();
