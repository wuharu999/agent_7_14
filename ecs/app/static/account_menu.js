(function () {
  'use strict';

  const labels = {
    'zh-CN': {settings:'设置', accountSettings:'用户设置', manage:'管理源文件', upload:'上传文档', exportWiki:'导出到我的AI', userManagement:'用户与权限管理', signOut:'退出登录', signIn:'登录', exportFailed:'导出失败'},
    'zh-TW': {settings:'設定', accountSettings:'使用者設定', manage:'管理來源', upload:'上傳文件', exportWiki:'導出到我的AI', userManagement:'使用者與權限管理', signOut:'登出', signIn:'登入', exportFailed:'導出失敗'},
    'ko': {settings:'설정', accountSettings:'사용자 설정', manage:'소스 관리', upload:'문서 업로드', exportWiki:'내 AI로 내보내기', userManagement:'사용자 및 권한 관리', signOut:'로그아웃', signIn:'로그인', exportFailed:'내보내기 실패'},
    'ja': {settings:'設定', accountSettings:'ユーザー設定', manage:'ソースの管理', upload:'ドキュメントのアップロード', exportWiki:'マイAIにエクスポート', userManagement:'ユーザーと権限の管理', signOut:'サインアウト', signIn:'サインイン', exportFailed:'エクスポート失敗'},
    'en': {settings:'Settings', accountSettings:'User settings', manage:'Manage sources', upload:'Upload documentation', exportWiki:'Export to my AI', userManagement:'User management', signOut:'Sign out', signIn:'Sign in', exportFailed:'Export failed'},
    'pt': {settings:'Configurações', accountSettings:'Configurações do usuário', manage:'Gerenciar fontes', upload:'Enviar documentação', exportWiki:'Exportar para minha IA', userManagement:'Gerenciamento de usuários', signOut:'Sair', signIn:'Entrar', exportFailed:'Falha na exportação'},
    'ru': {settings:'Настройки', accountSettings:'Настройки пользователя', manage:'Управление источниками', upload:'Загрузить документацию', exportWiki:'Экспорт в мой ИИ', userManagement:'Управление пользователями', signOut:'Выйти', signIn:'Войти', exportFailed:'Ошибка экспорта'},
    'es': {settings:'Configuración', accountSettings:'Configuración de usuario', manage:'Administrar fuentes', upload:'Subir documentación', exportWiki:'Exportar a mi IA', userManagement:'Gestión de usuarios', signOut:'Cerrar sesión', signIn:'Iniciar sesión', exportFailed:'Error de exportación'}
  };
  const navigationLabels = {
    'zh-CN': {questions:'知识问答', navigation:'页面导航', accountMenu:'账户菜单'},
    'zh-TW': {questions:'知識問答', navigation:'頁面導覽', accountMenu:'帳戶選單'},
    en: {questions:'Questions', navigation:'Page navigation', accountMenu:'Account menu'},
    ko: {questions:'지식 Q&A', navigation:'페이지 탐색', accountMenu:'계정 메뉴'},
    ja: {questions:'ナレッジQ&A', navigation:'ページナビゲーション', accountMenu:'アカウントメニュー'},
    pt: {questions:'Perguntas', navigation:'Navegação de páginas', accountMenu:'Menu da conta'},
    ru: {questions:'Вопросы', navigation:'Навигация по страницам', accountMenu:'Меню аккаунта'},
    es: {questions:'Preguntas', navigation:'Navegación de páginas', accountMenu:'Menú de cuenta'}
  };
  for (const language of Object.keys(labels)) Object.assign(labels[language], navigationLabels[language]);
  const toolLabels = {
    'zh-CN':['日志分析','场景推演'], 'zh-TW':['日誌分析','場景推演'],
    en:['Log analysis','Scenario analysis'], ko:['로그 분석','시나리오 분석'],
    ja:['ログ分析','シナリオ分析'], pt:['Análise de logs','Análise de cenários'],
    ru:['Анализ журналов','Анализ сценариев'], es:['Análisis de registros','Análisis de escenarios']
  };
  for (const [language, [logAnalysis, scenarioAnalysis]] of Object.entries(toolLabels)) {
    Object.assign(labels[language], {logAnalysis, scenarioAnalysis});
  }
  const appUrl = path => typeof window.appUrl === 'function' ? window.appUrl(path) : path;

  function selectedLanguage() {
    const selector = document.querySelector('#language, #langSelect, #ui-language');
    const candidate = selector && selector.value ? selector.value : document.documentElement.lang;
    if (labels[candidate]) return candidate;
    if (String(candidate || '').toLowerCase().startsWith('zh')) return 'zh-CN';
    return 'en';
  }

  function makeElement(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function initializeNavigation(user) {
    const nav = document.querySelector('[data-site-nav]');
    if (!nav) return;
    const destinations = [['/', 'questions'], ['/manage', 'manage'], ['/upload', 'upload']];
    if (user.tools_enabled && ['editor', 'admin'].includes(user.role)) {
      destinations.splice(1, 0, ['/tools/log', 'logAnalysis'], ['/tools/grill', 'scenarioAnalysis']);
    }
    if (user.role === 'admin') destinations.push(['/admin/users', 'userManagement']);
    destinations.push(['/settings', 'accountSettings']);
    const root = String(window.__APP_ROOT__ || '');
    let path = window.location.pathname;
    if (root && (path === root || path.startsWith(root + '/'))) path = path.slice(root.length);
    path = path.replace(/\/$/, '') || '/';
    // A batch's progress belongs to the upload section.
    const activePath = path.startsWith('/uploads/') ? '/upload' : path;
    for (const [destination, label] of destinations) {
      const link = makeElement('a', 'site-nav-link');
      link.href = appUrl(destination);
      link.dataset.accountLabel = label;
      if (destination === activePath) link.setAttribute('aria-current', 'page');
      nav.appendChild(link);
    }
    nav.hidden = false;
    // Keep links as native navigation: modifier-click and opening a new tab work.
    nav.addEventListener('click', event => {
      const link = event.target.closest('a');
      if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      if (link.getAttribute('aria-current') === 'page' && path === activePath) {
        event.preventDefault();
        return;
      }
      nav.querySelectorAll('.is-pending').forEach(item => item.classList.remove('is-pending'));
      link.classList.add('is-pending');
    });
    nav.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
      const links = [...nav.querySelectorAll('a')];
      const index = links.indexOf(document.activeElement);
      if (index < 0) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? links.length - 1
        : (index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1) + links.length) % links.length;
      links[next].focus();
    });
    window.addEventListener('pageshow', () => {
      nav.querySelectorAll('.is-pending').forEach(item => item.classList.remove('is-pending'));
    });
  }

  async function exportWiki(button) {
    const text = labels[selectedLanguage()] || labels.en;
    button.disabled = true;
    try {
      const response = await fetch('/api/export/wiki');
      if (!response.ok) {
        let message = text.exportFailed;
        try {
          const data = await response.json();
          message = data.error || message;
        } catch (_) {}
        throw new Error(message);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'wiki_export.zip';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      alert(error instanceof Error ? error.message : text.exportFailed);
    } finally {
      button.disabled = false;
    }
  }

  async function signOut(csrfToken, returnPath) {
    try {
      await fetch('/logout', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: new URLSearchParams({csrf_token: csrfToken})
      });
    } finally {
      window.location.href = appUrl(returnPath);
    }
  }

  function initializeAuthenticatedMenu(host, user) {
    initializeNavigation(user);
    host.innerHTML = '';
    const trigger = makeElement('button', 'account-menu-trigger');
    trigger.type = 'button';
    trigger.setAttribute('aria-haspopup', 'menu');
    trigger.setAttribute('aria-expanded', 'false');
    const username = String(user.username || 'User');
    trigger.append(
      makeElement('span', 'account-menu-avatar', username.slice(0, 1) || 'U'),
      makeElement('span', 'account-menu-username', username),
      makeElement('span', 'account-menu-chevron', '⌄')
    );

    const popover = makeElement('div', 'account-menu-popover');
    popover.role = 'menu';
    popover.hidden = true;
    const accountSettings = makeElement('a', 'account-menu-item');
    accountSettings.href = appUrl('/settings');
    accountSettings.role = 'menuitem';
    accountSettings.dataset.accountLabel = 'accountSettings';
    popover.appendChild(accountSettings);

    const exportButton = makeElement('button', 'account-menu-item');
    exportButton.type = 'button';
    exportButton.role = 'menuitem';
    exportButton.dataset.accountLabel = 'exportWiki';
    exportButton.addEventListener('click', () => exportWiki(exportButton));
    popover.appendChild(exportButton);

    popover.appendChild(makeElement('div', 'account-menu-divider'));
    const logout = makeElement('button', 'account-menu-item account-menu-danger');
    logout.type = 'button';
    logout.role = 'menuitem';
    logout.dataset.accountLabel = 'signOut';
    logout.addEventListener('click', () => signOut(String(user.csrf_token || ''), host.dataset.accountReturn || '/'));
    popover.appendChild(logout);
    host.append(trigger, popover);

    function setOpen(open) {
      popover.hidden = !open;
      trigger.setAttribute('aria-expanded', String(open));
    }

    function updateLabels() {
      const text = labels[selectedLanguage()] || labels.en;
      document.querySelectorAll('[data-account-label]').forEach(element => {
        const value = text[element.dataset.accountLabel];
        if (value) element.textContent = value;
      });
      trigger.setAttribute('aria-label', text.accountMenu);
      const nav = document.querySelector('[data-site-nav]');
      if (nav) {
        nav.setAttribute('aria-label', text.navigation);
        const current = nav.querySelector('[aria-current="page"]');
        // Scroll only the navigation strip, never the page or the chat history.
        if (current && !nav.classList.contains('site-nav-sidebar')) {
          nav.scrollLeft = current.offsetLeft - nav.offsetLeft - (nav.clientWidth - current.offsetWidth) / 2;
        }
      }
    }

    trigger.addEventListener('click', event => {
      event.stopPropagation();
      setOpen(popover.hidden);
    });
    trigger.addEventListener('keydown', event => {
      if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      event.preventDefault();
      setOpen(true);
      const items = popover.querySelectorAll('[role="menuitem"]');
      items[event.key === 'ArrowUp' ? items.length - 1 : 0].focus();
    });
    popover.addEventListener('keydown', event => {
      const items = [...popover.querySelectorAll('[role="menuitem"]:not(:disabled)')];
      const index = items.indexOf(document.activeElement);
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
          : (index + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length;
        items[next].focus();
      }
    });
    host.addEventListener('focusout', event => {
      if (!host.contains(event.relatedTarget)) setOpen(false);
    });
    document.addEventListener('click', event => {
      if (!host.contains(event.target)) setOpen(false);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !popover.hidden) {
        setOpen(false);
        trigger.focus();
      }
    });
    document.addEventListener('change', event => {
      if (event.target.matches('#language, #langSelect, #ui-language')) updateLabels();
    });
    updateLabels();
  }

  async function initialize(host) {
    host.classList.add('account-menu-host');
    try {
      const response = await fetch('/api/me');
      const user = await response.json();
      if (user.logged_in) {
        initializeAuthenticatedMenu(host, user);
        return;
      }
    } catch (_) {}

    const login = makeElement('a', 'account-menu-login');
    login.href = appUrl('/login');
    login.textContent = (labels[selectedLanguage()] || labels.en).signIn;
    host.replaceChildren(login);
  }

  function initializeAll() {
    document.querySelectorAll('[data-account-menu]').forEach(initialize);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeAll, {once: true});
  } else {
    initializeAll();
  }
})();
