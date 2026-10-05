import { t } from './i18n';

export type PortalAccount = {enabled: boolean; username?: string; role?: string; csrf_token?: string; portal_url?: string};
let account: PortalAccount = {enabled: false};

export function getPortalAccount(): PortalAccount { return account; }

export function portalCsrfToken(): string { return account.csrf_token || ''; }

function signInUrl(): string {
  const path = window.location.pathname;
  const destination = path === '/grill' || path.startsWith('/grill/') ? path : '/log';
  return account.portal_url + '/login?next=' + encodeURIComponent('/tools' + destination);
}

export async function initializePortal(): Promise<void> {
  const originalFetch = window.fetch.bind(window);
  const response = await originalFetch('/api/account', {cache: 'no-store'});
  if (!response.ok) {
    const data = await response.json();
    if (response.status === 401 && data.login_url) window.location.assign(data.login_url);
    throw new Error(t('Please sign in to access the analysis tools.', '请登录后使用分析工具。'));
  }
  account = await response.json();
  if (!account.enabled) return;
  window.fetch = async (input, init) => {
    const rawUrl = input instanceof Request ? input.url : String(input);
    const url = new URL(rawUrl, window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/')) return originalFetch(input, init);
    const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) headers.set('X-CSRF-Token', portalCsrfToken());
    const result = await originalFetch(input, {...init, headers});
    if (result.status === 401) window.location.assign(signInUrl());
    return result;
  };
}

export function appendPortalLinks(host: HTMLElement): void {
  if (!account.enabled || !account.portal_url) return;
  const group = document.createElement('nav');
  group.className = 'portal-links';
  group.setAttribute('aria-label', t('Knowledge portal', '知识平台'));
  const qa = document.createElement('a');
  qa.href = account.portal_url + '/';
  qa.className = 'button text-button';
  qa.textContent = t('Knowledge Q&A', '知识问答');
  const settings = document.createElement('a');
  settings.href = account.portal_url + '/settings';
  settings.className = 'button text-button portal-account';
  settings.textContent = account.username || t('Account', '账户');
  settings.title = t('Account settings', '用户设置');
  group.append(qa, settings);
  host.append(group);
}

export function appendPortalUserChip(host: HTMLElement): void {
  if (!account.enabled || !account.portal_url) return;
  const chip = document.createElement('a');
  chip.className = 'button text-button portal-account-chip';
  const portalBase = account.portal_url.replace(/\/+$/, '');
  chip.href = `${portalBase}/settings`;
  chip.title = t('User settings', '用户设置');
  const avatar = document.createElement('span');
  avatar.className = 'portal-avatar';
  const name = String(account.username || 'User');
  avatar.textContent = (name.slice(0, 1) || 'U').toUpperCase();
  const username = document.createElement('span');
  username.className = 'portal-username';
  username.textContent = name;
  chip.append(avatar, username);
  host.append(chip);
}
