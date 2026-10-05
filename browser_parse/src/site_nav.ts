import { t, uiLanguage, onUiLanguage } from './i18n';
import { getPortalAccount } from './portal';

export interface SiteNavOptions {
  activePath: '/log' | '/grill';
  navigate: (path: string) => void;
}

const labels: Record<string, Record<string, string>> = {
  'zh': {
    questions: '知识问答',
    logAnalysis: '日志分析',
    scenarioAnalysis: '场景推演',
    manage: '管理源文件',
    upload: '上传文档',
    userManagement: '用户与权限管理',
    accountSettings: '用户设置',
    navigation: '页面导航',
  },
  'en': {
    questions: 'Questions',
    logAnalysis: 'Log analysis',
    scenarioAnalysis: 'Scenario analysis',
    manage: 'Manage sources',
    upload: 'Upload documentation',
    userManagement: 'User management',
    accountSettings: 'User settings',
    navigation: 'Page navigation',
  }
};

export function createSiteNav(options: SiteNavOptions): HTMLElement {
  const account = getPortalAccount();
  const portalBase = (account.portal_url || '').replace(/\/+$/, '');

  const wrapper = document.createElement('div');
  wrapper.className = 'top-nav-wrapper';

  const nav = document.createElement('nav');
  nav.className = 'site-nav';
  nav.setAttribute('data-site-nav', '');
  nav.setAttribute('aria-label', t('Page navigation', '页面导航'));

  const destinations: Array<{
    key: string;
    path: string;
    isLocal: boolean;
    adminOnly?: boolean;
  }> = [
    { key: 'questions', path: `${portalBase}/`, isLocal: false },
    { key: 'logAnalysis', path: '/log', isLocal: true },
    { key: 'scenarioAnalysis', path: '/grill', isLocal: true },
    { key: 'manage', path: `${portalBase}/manage`, isLocal: false },
    { key: 'upload', path: `${portalBase}/upload`, isLocal: false },
    { key: 'userManagement', path: `${portalBase}/admin/users`, isLocal: false, adminOnly: true },
    { key: 'accountSettings', path: `${portalBase}/settings`, isLocal: false },
  ];

  const currentLang = uiLanguage() === 'zh' ? 'zh' : 'en';
  const langLabels = labels[currentLang] || labels['en'];

  for (const item of destinations) {
    if (!item.isLocal && !portalBase) continue;
    if (item.adminOnly && account.role !== 'admin') {
      continue;
    }
    const link = document.createElement('a');
    link.className = 'site-nav-link';
    link.href = item.path;
    link.textContent = langLabels[item.key] || item.key;
    link.dataset.navKey = item.key;

    if (item.isLocal && item.path === options.activePath) {
      link.setAttribute('aria-current', 'page');
    }

    link.addEventListener('click', (event) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      if (item.isLocal) {
        event.preventDefault();
        if (item.path !== options.activePath) {
          nav.querySelectorAll('.site-nav-link').forEach(l => l.classList.remove('is-pending'));
          link.classList.add('is-pending');
          options.navigate(item.path);
        }
      } else {
        nav.querySelectorAll('.site-nav-link').forEach(l => l.classList.remove('is-pending'));
        link.classList.add('is-pending');
      }
    });

    nav.appendChild(link);
  }

  // Keyboard navigation
  nav.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    const links = Array.from(nav.querySelectorAll<HTMLAnchorElement>('a'));
    const index = links.indexOf(document.activeElement as HTMLAnchorElement);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? links.length - 1
      : (index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + links.length) % links.length;
    links[next].focus();
  });

  // Re-translate on language change
  onUiLanguage(() => {
    const lang = uiLanguage() === 'zh' ? 'zh' : 'en';
    const dict = labels[lang] || labels['en'];
    nav.setAttribute('aria-label', dict.navigation);
    nav.querySelectorAll<HTMLAnchorElement>('[data-nav-key]').forEach((el) => {
      const key = el.dataset.navKey;
      if (key && dict[key]) {
        el.textContent = dict[key];
      }
    });
  });

  // Auto scroll active item into view
  requestAnimationFrame(() => {
    const current = nav.querySelector<HTMLElement>('[aria-current="page"]');
    if (current) {
      nav.scrollLeft = current.offsetLeft - nav.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2;
    }
  });

  wrapper.appendChild(nav);
  return wrapper;
}
