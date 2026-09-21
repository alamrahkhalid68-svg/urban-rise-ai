(() => {
  const form = document.getElementById('featured-project-form');
  if (!form) return;
  const coverInput = document.getElementById('cover-image-input');
  const galleryInput = document.getElementById('gallery-images-input');
  const preview = document.getElementById('new-image-preview');
  const count = document.getElementById('selected-image-count');
  let gallery = [];
  let urls = [];
  const details = new WeakMap();

  function syncGallery() {
    const transfer = new DataTransfer();
    gallery.forEach(file => transfer.items.add(file));
    galleryInput.files = transfer.files;
  }

  function selectedCover() {
    return form.querySelector('input[name="cover_choice"]:checked')?.value || '';
  }

  function makeField(labelText, type, name, value, onInput) {
    const label = document.createElement('label');
    label.textContent = labelText;
    const input = document.createElement('input');
    input.type = type;
    input.name = name;
    input.value = value;
    if (type === 'text') input.maxLength = 180;
    if (type === 'number') { input.min = -100000; input.max = 100000; }
    input.addEventListener('input', onInput);
    label.append(input);
    return label;
  }

  function render(preferredFile = null) {
    const former = selectedCover();
    urls.forEach(URL.revokeObjectURL);
    urls = [];
    preview.replaceChildren();
    const files = [...(coverInput.files[0] ? [coverInput.files[0]] : []), ...gallery];
    count.textContent = `الصور الجديدة المختارة: ${files.length}`;
    files.forEach((file, index) => {
      const state = details.get(file) || {alt: '', order: String(index)};
      details.set(file, state);
      const card = document.createElement('article');
      card.className = 'admin-image-card';
      const image = document.createElement('img');
      const url = URL.createObjectURL(file);
      urls.push(url);
      image.src = url;
      image.alt = `معاينة ${file.name}`;
      const title = document.createElement('strong');
      title.textContent = file.name;
      const radioLabel = document.createElement('label');
      radioLabel.className = 'admin-radio';
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'cover_choice';
      radio.value = `new-${index}`;
      radio.checked = file === preferredFile || (!preferredFile && former === radio.value);
      radioLabel.append(radio, document.createTextNode(' صورة الغلاف'));
      const alt = makeField('وصف الصورة', 'text', `new_alt_${index}`, state.alt, event => { state.alt = event.target.value; });
      const order = makeField('ترتيب الصورة', 'number', `new_order_${index}`, state.order, event => { state.order = event.target.value; });
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'danger';
      remove.textContent = 'حذف من المعاينة';
      remove.addEventListener('click', () => {
        const wasCover = radio.checked;
        const selectedFile = files.find((_, position) => preview.querySelector(`input[value="new-${position}"]`)?.checked);
        if (file === coverInput.files[0]) coverInput.value = '';
        else { gallery = gallery.filter(item => item !== file); syncGallery(); }
        render(wasCover ? null : selectedFile);
        if (wasCover) chooseAvailableCover();
      });
      card.append(image, title, radioLabel, alt, order, remove);
      preview.append(card);
    });
    if (preferredFile) preview.querySelector(`input[name="cover_choice"][value="new-${files.indexOf(preferredFile)}"]`)?.click();
  }

  function chooseAvailableCover() {
    if (selectedCover()) return;
    const existing = [...form.querySelectorAll('[data-existing-image]')]
      .find(card => !card.querySelector('input[name="remove_image"]').checked);
    (existing?.querySelector('input[name="cover_choice"]') || preview.querySelector('input[name="cover_choice"]'))?.click();
  }

  coverInput.addEventListener('change', () => render(coverInput.files[0] || null));
  galleryInput.addEventListener('change', () => {
    const chosen = [...galleryInput.files];
    gallery.push(...chosen);
    syncGallery();
    render();
    chooseAvailableCover();
  });
  form.querySelectorAll('input[name="remove_image"]').forEach(box => box.addEventListener('change', () => {
    box.closest('.admin-image-card').classList.toggle('is-removed', box.checked);
    if (box.checked && box.closest('.admin-image-card').querySelector('input[name="cover_choice"]').checked) {
      box.closest('.admin-image-card').querySelector('input[name="cover_choice"]').checked = false;
      chooseAvailableCover();
    }
  }));
  form.addEventListener('submit', event => {
    if (form.elements.status.value !== 'published') return;
    const choice = selectedCover();
    if (!choice || (choice.startsWith('existing-') && form.querySelector(`input[name="remove_image"][value="${choice.slice(9)}"]`)?.checked)) {
      event.preventDefault();
      alert('لا يمكن نشر المشروع دون صورة غلاف. أرفق صورة واخترها غلافًا.');
    }
  });
})();
