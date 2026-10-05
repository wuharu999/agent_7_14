import { describe, it, expect, vi, beforeEach } from 'vitest';
import { setUiLanguage } from '../src/i18n';
import * as portalModule from '../src/portal';

class MockElement {
  tagName: string;
  className = '';
  href = '';
  textContent = '';
  dataset: Record<string, string> = {};
  attributes = new Map<string, string>();
  children: MockElement[] = [];
  eventListeners: Record<string, Function[]> = {};
  classList = {
    add: (c: string) => { if (!this.className.includes(c)) this.className += ` ${c}`; },
    remove: (c: string) => { this.className = this.className.replace(c, '').trim(); },
    contains: (c: string) => this.className.includes(c),
  };

  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }

  setAttribute(k: string, v: string) { this.attributes.set(k, v); }
  getAttribute(k: string) { return this.attributes.get(k) ?? null; }
  appendChild(child: MockElement) { this.children.push(child); }
  addEventListener(event: string, fn: Function) {
    this.eventListeners[event] = this.eventListeners[event] || [];
    this.eventListeners[event].push(fn);
  }

  querySelectorAll(sel: string): MockElement[] {
    const res: MockElement[] = [];
    const check = (node: MockElement) => {
      if (sel === '.site-nav-link' || sel.includes('a')) {
        if (node.className.includes('site-nav-link')) res.push(node);
      }
      if (sel.includes('[data-nav-key]') && node.dataset.navKey) {
        res.push(node);
      }
      for (const c of node.children) check(c);
    };
    for (const c of this.children) check(c);
    return res;
  }

  querySelector(sel: string): MockElement | null {
    if (sel.includes('[aria-current="page"]')) {
      const findActive = (node: MockElement): MockElement | null => {
        if (node.getAttribute('aria-current') === 'page') return node;
        for (const c of node.children) {
          const found = findActive(c);
          if (found) return found;
        }
        return null;
      };
      return findActive(this);
    }
    const list = this.querySelectorAll(sel);
    return list[0] ?? null;
  }

  click() {
    for (const fn of (this.eventListeners['click'] || [])) {
      fn({ button: 0, preventDefault: () => {} });
    }
  }
}

// Mock globals for node environment
(globalThis as any).document = {
  createElement: (tag: string) => new MockElement(tag),
  documentElement: { lang: 'zh' },
};
(globalThis as any).requestAnimationFrame = (fn: (time: number) => void) => {
  return setTimeout(() => fn(Date.now()), 0);
};

// Import after mocking document
const { createSiteNav } = await import('../src/site_nav');

describe('site_nav component', () => {
  beforeEach(() => {
    setUiLanguage('zh');
  });

  it('renders all 7 navigation tabs for admin user', () => {
    vi.spyOn(portalModule, 'getPortalAccount').mockReturnValue({
      enabled: true,
      role: 'admin',
      username: 'admin',
      portal_url: 'http://120.77.250.227:8000/v1/faq-platform',
    });

    const navigate = vi.fn();
    const wrapper = createSiteNav({ activePath: '/log', navigate }) as any;
    const links = wrapper.querySelectorAll('.site-nav-link');
    expect(links.length).toBe(7);

    const labels = links.map((l: any) => l.textContent.trim());
    expect(labels).toEqual([
      '知识问答',
      '日志分析',
      '场景推演',
      '管理源文件',
      '上传文档',
      '用户与权限管理',
      '用户设置',
    ]);

    const activeLink = wrapper.querySelector('.site-nav-link[aria-current="page"]');
    expect(activeLink).not.toBeNull();
    expect(activeLink?.textContent.trim()).toBe('日志分析');
  });

  it('omits userManagement tab for editor role', () => {
    vi.spyOn(portalModule, 'getPortalAccount').mockReturnValue({
      enabled: true,
      role: 'editor',
      username: 'editor',
      portal_url: 'http://120.77.250.227:8000/v1/faq-platform',
    });

    const navigate = vi.fn();
    const wrapper = createSiteNav({ activePath: '/grill', navigate }) as any;
    const links = wrapper.querySelectorAll('.site-nav-link');
    expect(links.length).toBe(6);

    const activeLink = wrapper.querySelector('.site-nav-link[aria-current="page"]');
    expect(activeLink?.textContent.trim()).toBe('场景推演');
  });

  it('calls navigate when clicking another local tool tab', () => {
    vi.spyOn(portalModule, 'getPortalAccount').mockReturnValue({
      enabled: true,
      role: 'admin',
      username: 'admin',
      portal_url: 'http://120.77.250.227:8000/v1/faq-platform',
    });

    const navigate = vi.fn();
    const wrapper = createSiteNav({ activePath: '/log', navigate }) as any;
    const grillLink = wrapper.querySelectorAll('.site-nav-link')
      .find((l: any) => l.dataset.navKey === 'scenarioAnalysis');

    expect(grillLink).toBeDefined();
    grillLink?.click();
    expect(navigate).toHaveBeenCalledWith('/grill');
  });

  it('switches text dynamically when UI language changes', () => {
    vi.spyOn(portalModule, 'getPortalAccount').mockReturnValue({
      enabled: true,
      role: 'admin',
      username: 'admin',
      portal_url: 'http://120.77.250.227:8000/v1/faq-platform',
    });

    const navigate = vi.fn();
    const wrapper = createSiteNav({ activePath: '/log', navigate }) as any;

    setUiLanguage('en');
    const linksEn = wrapper.querySelectorAll('.site-nav-link');
    const labelsEn = linksEn.map((l: any) => l.textContent.trim());
    expect(labelsEn).toEqual([
      'Questions',
      'Log analysis',
      'Scenario analysis',
      'Manage sources',
      'Upload documentation',
      'User management',
      'User settings',
    ]);
  });
});
