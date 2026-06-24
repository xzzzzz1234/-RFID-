document.addEventListener('click', function (event) {
  const target = event.target.closest('[data-confirm]');
  if (target && !confirm(target.dataset.confirm)) {
    event.preventDefault();
  }
});
