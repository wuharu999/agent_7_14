(function () {
  'use strict';

  const labels = {
    'zh-CN': {settings:'设置', account:'账户', accountSettings:'用户设置', manage:'管理源文件', upload:'上传文档', exportWiki:'导出到我的AI', userManagement:'用户与权限管理', signOut:'退出登录', signIn:'登录', exportFailed:'导出失败'},
    'zh-TW': {settings:'設定', account:'帳戶', accountSettings:'使用者設定', manage:'管理來源', upload:'上傳文件', exportWiki:'導出到我的AI', userManagement:'使用者與權限管理', signOut:'登出', signIn:'登入', exportFailed:'導出失敗'},
    'ko': {settings:'설정', account:'계정', accountSettings:'사용자 설정', manage:'소스 관리', upload:'문서 업로드', exportWiki:'내 AI로 내보내기', userManagement:'사용자 및 권한 관리', signOut:'로그아웃', signIn:'로그인', exportFailed:'내보내기 실패'},
    'ja': {settings:'設定', account:'アカウント', accountSettings:'ユーザー設定', manage:'ソースの管理', upload:'ドキュメントのアップロード', exportWiki:'マイAIにエクスポート', userManagement:'ユーザーと権限の管理', signOut:'サインアウト', signIn:'サインイン', exportFailed:'エクスポート失敗'},
    'en': {settings:'Settings', account:'Account', accountSettings:'User settings', manage:'Manage sources', upload:'Upload documentation', exportWiki:'Export to my AI', userManagement:'User management', signOut:'Sign out', signIn:'Sign in', exportFailed:'Export failed'},
    'pt': {settings:'Configurações', account:'Conta', accountSettings:'Configurações do usuário', manage:'Gerenciar fontes', upload:'Enviar documentação', exportWiki:'Exportar para minha IA', userManagement:'Gerenciamento de usuários', signOut:'Sair', signIn:'Entrar', exportFailed:'Falha na exportação'},
    'ru': {settings:'Настройки', account:'Аккаунт', accountSettings:'Настройки пользователя', manage:'Управление источниками', upload:'Загрузить документацию', exportWiki:'Экспорт в мой ИИ', userManagement:'Управление пользователями', signOut:'Выйти', signIn:'Войти', exportFailed:'Ошибка экспорта'},
    'es': {settings:'Configuración', account:'Cuenta', accountSettings:'Configuración de usuario', manage:'Administrar fuentes', upload:'Subir documentación', exportWiki:'Exportar a mi IA', userManagement:'Gestión de usuarios', signOut:'Cerrar sesión', signIn:'Iniciar sesión', exportFailed:'Error de exportación'}
  };

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
      window.location.href = returnPath;
    }
  }

  function initializeAuthenticatedMenu(host, user) {
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
    const heading = makeElement('a', 'account-menu-heading');
    heading.href = '/settings';
    heading.role = 'menuitem';
    heading.dataset.accountLabel = 'account';
    popover.appendChild(heading);

    const accountSettings = makeElement('a', 'account-menu-item');
    accountSettings.href = '/settings';
    accountSettings.role = 'menuitem';
    accountSettings.dataset.accountLabel = 'accountSettings';
    accountSettings.addEventListener('click', event => {
      event.preventDefault();
      window.location.assign('/settings');
    });
    popover.appendChild(accountSettings);

    const manage = makeElement('a', 'account-menu-item');
    manage.href = '/manage';
    manage.role = 'menuitem';
    manage.dataset.accountLabel = 'manage';
    popover.appendChild(manage);

    const upload = makeElement('a', 'account-menu-item');
    upload.href = '/upload';
    upload.role = 'menuitem';
    upload.dataset.accountLabel = 'upload';
    popover.appendChild(upload);

    const exportButton = makeElement('button', 'account-menu-item');
    exportButton.type = 'button';
    exportButton.role = 'menuitem';
    exportButton.dataset.accountLabel = 'exportWiki';
    exportButton.addEventListener('click', () => exportWiki(exportButton));
    popover.appendChild(exportButton);

    if (user.role === 'admin') {
      const users = makeElement('a', 'account-menu-item');
      users.href = '/admin/users';
      users.role = 'menuitem';
      users.dataset.accountLabel = 'userManagement';
      popover.appendChild(users);
    }

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
      host.querySelectorAll('[data-account-label]').forEach(element => {
        const value = text[element.dataset.accountLabel];
        if (value) element.textContent = value;
      });
      trigger.setAttribute('aria-label', text.settings);
    }

    trigger.addEventListener('click', event => {
      event.stopPropagation();
      setOpen(popover.hidden);
    });
    document.addEventListener('click', event => {
      if (!host.contains(event.target)) setOpen(false);
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') setOpen(false);
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
    login.href = '/login';
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
