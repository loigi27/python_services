document.querySelectorAll('input[type="file"][data-file-label]').forEach(input => {
  input.addEventListener('change', () => {
    const label = document.getElementById(input.dataset.fileLabel);
    if (!label) return;
    const files = Array.from(input.files || []);
    label.textContent = !files.length ? '' : `${files.length} arquivo(s): ${files.map(f => f.name).join(', ')}`;
    if (files.length > 5) label.textContent += ' — limite: 5 arquivos';
  });
});
