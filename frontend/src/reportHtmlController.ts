// Self-contained: this function is embedded in the downloaded offline report.
export function initOfflineReport() {
  const summary = document.getElementById('report-summary')!;
  const details = document.getElementById('report-details')!;
  const selector = document.getElementById('report-section') as HTMLSelectElement;
  const tabs = document.querySelectorAll<HTMLButtonElement>('[data-report-view]');
  const selectSection = () => {
    document.querySelectorAll<HTMLElement>('[data-report-section]').forEach((section) => {
      section.hidden = section.dataset.reportSection !== selector.value;
    });
  };
  tabs.forEach((button) => button.addEventListener('click', () => {
    const isSummary = button.dataset.reportView === 'summary';
    summary.hidden = !isSummary;
    details.hidden = isSummary;
    tabs.forEach((tab) => tab.setAttribute('aria-pressed', String(tab === button)));
  }));
  selector.addEventListener('change', selectSection);
  selectSection();
  document.getElementById('download-markdown')!.addEventListener('click', () => {
    const payload = JSON.parse(document.getElementById('report-source')!.textContent!);
    const url = URL.createObjectURL(new Blob([payload.text], { type: 'text/markdown;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'report.zh-CN.md';
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
