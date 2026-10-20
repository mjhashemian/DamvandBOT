import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { LayoutDashboard, Users, Package, Receipt, Tags, BadgePercent, Settings, ScrollText, Activity, LogOut, Languages, Menu, Mountain, ToggleRight } from 'lucide-react'
import { Button } from './components/ui/button'
import { Sidebar, SidebarHeader, SidebarContent, SidebarFooter, SidebarMenuButton } from './components/ui/sidebar'
import './index.css'
import './secret.css'

type Language = 'en' | 'fa'
const routes = [
  ['/', 'Overview', 'نمای کلی', LayoutDashboard],
  ['/users', 'Users', 'کاربران', Users],
  ['/services', 'Assigned services', 'سرویس‌های اختصاصی', Package],
  ['/orders', 'Orders', 'سفارش‌ها', Receipt],
  ['/plans', 'Plans', 'پلن‌ها', Tags],
  ['/discounts', 'Discounts', 'تخفیف‌ها', BadgePercent],
  ['/buttons', 'Bot buttons', 'دکمه‌های ربات', ToggleRight],
  ['/settings', 'Settings', 'تنظیمات', Settings],
  ['/terms', 'Terms and conditions', 'قوانین و شرایط', ScrollText],
  ['/audit', 'Audit', 'گزارش فعالیت', Activity],
] as const

const translations: Record<string, string> = {
  'Overview':'نمای کلی','Users':'کاربران','Assigned services':'سرویس‌های اختصاصی','Orders':'سفارش‌ها','Plans':'پلن‌ها','Discounts':'تخفیف‌ها','Bot buttons':'دکمه‌های ربات','Settings':'تنظیمات','Terms and conditions':'قوانین و شرایط','Audit':'گزارش فعالیت',
  'Sign in':'ورود','Panel password':'رمز پنل','Invalid password.':'رمز نادرست است.','Try again':'دوباره تلاش کنید',
  'Bot settings':'تنظیمات ربات','Payment settings':'تنظیمات پرداخت','Channel settings':'تنظیمات کانال','Trial settings':'تنظیمات سرویس آزمایشی',
  'Welcome settings':'تنظیمات خوش‌آمدگویی','Before-START description':'توضیحات پیش از شروع','Welcome message':'پیام خوش‌آمدگویی',
  'The description appears before START. The banner and welcome message are sent after /start. Use {name} and {requirements} as optional placeholders in the welcome message.':'توضیحات پیش از START نمایش داده می‌شود. بنر و پیام خوش‌آمدگویی بعد از /start ارسال می‌شوند. در پیام می‌توانید از {name} و {requirements} استفاده کنید.',
  'Bot Token':'توکن ربات','Admin Telegram IDs':'شناسه‌های تلگرام مدیران','Support username':'نام کاربری پشتیبانی','3x-ui panel URL':'نشانی پنل 3x-ui','3x-ui API token':'توکن API پنل 3x-ui','Subscription base URL':'نشانی پایه اشتراک',
  'Channel username':'نام کاربری کانال','Channel ID':'شناسه کانال','Channel join link':'لینک عضویت کانال','Require channel membership':'الزام عضویت در کانال',
  'Card-to-card payment':'پرداخت کارت‌به‌کارت','Card number':'شماره کارت','Cardholder name':'نام صاحب کارت','Payment instructions':'راهنمای پرداخت',
  'Free trial':'سرویس آزمایشی','Trial traffic (MB)':'حجم آزمایشی (مگابایت)','Trial duration (days)':'مدت آزمایشی (روز)',
  'Save all settings':'ذخیره همه تنظیمات','Save plan':'ذخیره پلن','Add plan':'افزودن پلن','Persian title':'عنوان فارسی','Traffic GB':'حجم (گیگابایت)','Days':'روز','Toman':'تومان','Stars':'استارز','Enabled':'فعال','Yes':'بله','No':'خیر',
  'Assign an existing service':'اختصاص سرویس موجود','Telegram ID or @username':'شناسه تلگرام یا نام کاربری','Plan':'پلن','Service label (optional)':'نام سرویس (اختیاری)','Subscription link':'لینک اشتراک','Assign service':'اختصاص سرویس','Remove':'حذف',
  'Create discount':'ساخت تخفیف','Code':'کد','Percentage':'درصد','Specific user (blank = all users)':'کاربر مشخص (خالی = همه کاربران)','Save discount':'ذخیره تخفیف','Disable':'غیرفعال‌سازی','Enable':'فعال‌سازی',
  'ID':'شناسه','Telegram ID':'شناسه تلگرام','Username':'نام کاربری','Name':'نام','Terms':'قوانین','Joined':'تاریخ عضویت','User':'کاربر','Kind':'نوع','Status':'وضعیت','Created':'تاریخ ایجاد','Service':'سرویس','Link':'لینک','Percent':'درصد','Time':'زمان','Event':'رویداد','Details':'جزئیات','Discount':'تخفیف',
  'Recent orders':'سفارش‌های اخیر','Latest 200 users':'۲۰۰ کاربر اخیر','Save terms':'ذخیره قوانین','Finish setup':'تکمیل راه‌اندازی',
  'Disabled':'غیرفعال','No plan label':'بدون نام پلن','All plans':'همه پلن‌ها','All users':'همه کاربران',
  'Enter the bot and 3x-ui details to finish setup. Channel membership is optional.':'برای تکمیل راه‌اندازی، اطلاعات ربات و 3x-ui را وارد کنید. الزام عضویت در کانال اختیاری است.',
  'Saved values remain here after refresh. Secret tokens are hidden; leave them blank to keep the saved value.':'تنظیمات پس از تازه‌سازی باقی می‌مانند. توکن‌ها نمایش داده نمی‌شوند؛ برای حفظ مقدار قبلی، آن‌ها را خالی بگذارید.',
  'If the @username has not started the bot, this link waits here and attaches automatically on their first /start. Telegram usernames can change or be reassigned before then; use a numeric ID when available.':'اگر این نام کاربری هنوز ربات را شروع نکرده باشد، لینک تا نخستین /start در انتظار می‌ماند و سپس خودکار به حسابش متصل می‌شود. نام کاربری تلگرام ممکن است تغییر کند یا به فرد دیگری برسد؛ در صورت امکان از شناسه عددی استفاده کنید.',
  'This adds the link to My Services. It does not edit the 3x-ui client or enable renewal for this imported service.':'این کار لینک را به بخش «سرویس‌های من» اضافه می‌کند، اما کلاینت 3x-ui را ویرایش نمی‌کند و تمدید این سرویس واردشده را فعال نمی‌کند.',
  'Waiting for first /start':'در انتظار اولین /start','Assigned to Telegram accounts':'متصل به حساب‌های تلگرام',
  'Waiting for username to start the bot':'در انتظار شروع ربات توسط این نام کاربری','Service removed':'سرویس حذف شد','Existing 3x-ui service':'سرویس موجود 3x-ui',
  'A username must already be known to the bot. Use a numeric Telegram ID for users who have not started it yet.':'نام کاربری باید از قبل برای ربات شناخته‌شده باشد. برای افراد جدید از شناسه عددی تلگرام استفاده کنید.',
  'Saving changes the version and asks users to accept again.':'با ذخیره تغییرات، کاربران باید قوانین را دوباره بپذیرند.',
  'Choose which buttons users see in the bot. Disabled buttons and their entry points stop accepting new requests; existing orders can still be completed.':'انتخاب کنید کاربران کدام دکمه‌ها را در ربات ببینند. دکمه‌های غیرفعال درخواست جدید نمی‌پذیرند؛ سفارش‌های قبلی همچنان قابل تکمیل‌اند.',
  'The Channel button is hidden whenever the channel membership requirement is disabled in Settings. Hiding the Channel button alone does not turn off that requirement.':'وقتی الزام عضویت در کانال در تنظیمات خاموش باشد، دکمه کانال هم پنهان می‌شود. پنهان کردن دکمه کانال به‌تنهایی الزام عضویت را خاموش نمی‌کند.',
  'Save bot buttons':'ذخیره دکمه‌های ربات','Bot buttons saved; bot restart requested':'دکمه‌های ربات ذخیره شدند؛ ربات در حال راه‌اندازی مجدد است',
  'Service assignment saved':'سرویس اختصاص داده شد','Settings saved; bot restart requested':'تنظیمات ذخیره شد؛ ربات در حال راه‌اندازی مجدد است',
  'Saved — leave blank to keep current':'ذخیره شده؛ برای حفظ مقدار قبلی خالی بگذارید',
  'Show':'نمایش','Hide':'پنهان‌سازی',
  'Payment receipts must be verified against your bank account before approval in Telegram.':'پیش از تأیید رسید در تلگرام، واریز را در حساب بانکی خود بررسی کنید.',
  'users':'کاربران','orders':'سفارش‌ها','services':'سرویس‌ها','trials':'آزمایشی‌ها','Receipts awaiting review':'رسیدهای در انتظار بررسی',
  'Latest 200 orders. Receipt approval and retries remain in the bot, where provisioning is transactional.':'۲۰۰ سفارش اخیر. تأیید رسید و تلاش مجدد برای ساخت سرویس همچنان در ربات انجام می‌شود.',
  'Plan saved':'پلن ذخیره شد','Discount updated':'تخفیف به‌روزرسانی شد','Terms saved; users must accept again':'قوانین ذخیره شد؛ کاربران باید دوباره آن را بپذیرند',
  'Active':'فعال','pending':'در انتظار','processing':'در حال پردازش','paid':'پرداخت‌شده','paid_error':'خطای ساخت','card_pending_review':'در انتظار بررسی رسید','completed':'تکمیل‌شده','failed':'ناموفق',
  'buy':'خرید','renew':'تمدید','trial':'آزمایشی','Saved':'ذخیره‌شده',
  'The panel is ready. Add the Telegram bot token, admin IDs, channel, 3x-ui API and payment settings in':'پنل آماده است. توکن ربات، شناسه مدیران، کانال، API پنل 3x-ui و تنظیمات پرداخت را در',
  '. The bot will start after valid settings are saved.':' وارد کنید. ربات پس از ذخیره تنظیمات معتبر شروع می‌شود.',
}

function translateContent(language: Language) {
  document.documentElement.lang = language
  document.documentElement.dir = language === 'fa' ? 'rtl' : 'ltr'
  const main = document.querySelector('main')
  if (!main) return
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT)
  let node: Node | null
  while ((node = walker.nextNode())) {
    const parent = node.parentElement
    if (!parent || parent.closest('code,pre,textarea,script,style')) continue
    const original = (node as Text & { originalText?: string }).originalText ?? node.textContent ?? ''
    ;(node as Text & { originalText?: string }).originalText = original
    const key = original.trim()
    if (!translations[key]) continue
    node.textContent = language === 'fa' ? original.replace(key, translations[key]) : original
  }
  main.querySelectorAll<HTMLInputElement>('[placeholder]').forEach(input => {
    const original = input.dataset.originalPlaceholder ?? input.placeholder
    input.dataset.originalPlaceholder = original
    input.placeholder = language === 'fa' ? (translations[original] ?? original) : original
  })
  main.querySelectorAll<HTMLButtonElement>('.reveal-secret').forEach(button => {
    const visible = button.parentElement?.querySelector('input')?.type === 'text'
    button.textContent = language === 'fa' ? (visible ? 'پنهان‌سازی' : 'نمایش') : (visible ? 'Hide' : 'Show')
  })
  document.title = `${main.querySelector('h1')?.textContent ?? 'Damavand'} · Damavand Bot`
}

document.addEventListener('click', async event => {
  const button = event.target instanceof Element ? event.target.closest<HTMLButtonElement>('button.reveal-secret') : null
  if (!button) return
  const input = button.parentElement?.querySelector<HTMLInputElement>('input')
  const csrf = button.closest('form')?.querySelector<HTMLInputElement>('input[name="csrf"]')?.value
  if (!input || !csrf) return
  if (input.type === 'text') {
    input.type = 'password'
    if (button.dataset.revealedFromServer === 'true') {
      input.value = ''
      delete button.dataset.revealedFromServer
    }
    button.textContent = document.documentElement.lang === 'fa' ? 'نمایش' : 'Show'
    return
  }
  button.disabled = true
  try {
    if (!input.value) {
      const response = await fetch('/settings/reveal', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ csrf, key: button.dataset.secretKey || '' }),
      })
      if (!response.ok || !response.headers.get('Content-Type')?.startsWith('application/json')) throw new Error('Reveal failed')
      const data: { value: string } = await response.json()
      input.value = data.value
      button.dataset.revealedFromServer = 'true'
    }
    input.type = 'text'
    button.textContent = document.documentElement.lang === 'fa' ? 'پنهان‌سازی' : 'Hide'
  } catch {
    alert(document.documentElement.lang === 'fa' ? 'نمایش توکن ممکن نشد. دوباره وارد پنل شوید و تلاش کنید.' : 'Could not reveal the token. Sign in again and retry.')
  } finally {
    button.disabled = false
  }
})

document.addEventListener('input', event => {
  const input = event.target instanceof HTMLInputElement ? event.target : null
  input?.closest('.secret-field')?.querySelector<HTMLButtonElement>('.reveal-secret')?.removeAttribute('data-revealed-from-server')
})

function App({ csrf }: { csrf: string }) {
  const [language, setLanguage] = useState<Language>(localStorage.getItem('damavand-language') === 'fa' ? 'fa' : 'en')
  const [open, setOpen] = useState(false)
  React.useEffect(() => { translateContent(language) }, [language])
  const active = location.pathname
  const switchLanguage = () => { const next = language === 'fa' ? 'en' : 'fa'; localStorage.setItem('damavand-language', next); setLanguage(next) }
  return <>
    <Button className="mobile-menu" variant="outline" size="icon" aria-label="Toggle menu" onClick={() => setOpen(!open)}><Menu size={20}/></Button>
    {open && <button className="sidebar-overlay" aria-label="Close menu" onClick={() => setOpen(false)}/>}
    <Sidebar className={open ? 'is-open' : ''}>
      <SidebarHeader><div className="brand-icon"><Mountain size={24}/></div><div><strong>Damavand</strong><small>Bot Management</small></div></SidebarHeader>
      <SidebarContent aria-label="Main navigation">
        <span className="nav-caption">{language === 'fa' ? 'مدیریت' : 'MANAGEMENT'}</span>
        {routes.map(([path, en, fa, Icon]) => <SidebarMenuButton key={path} href={path} active={active === path}><Icon size={18}/><span>{language === 'fa' ? fa : en}</span></SidebarMenuButton>)}
      </SidebarContent>
      <SidebarFooter>
        <Button variant="ghost" onClick={switchLanguage}><Languages size={18}/>{language === 'fa' ? 'English' : 'فارسی'}</Button>
        <form method="post" action="/logout"><input type="hidden" name="csrf" value={csrf}/><Button type="submit" variant="ghost"><LogOut size={18}/>{language === 'fa' ? 'خروج' : 'Sign out'}</Button></form>
      </SidebarFooter>
    </Sidebar>
  </>
}

const mount = document.getElementById('panel-sidebar')
if (mount) createRoot(mount).render(<App csrf={mount.dataset.csrf || ''}/>)
else translateContent(localStorage.getItem('damavand-language') === 'fa' ? 'fa' : 'en')
