import { useEffect, useState } from 'react'
import { ArrowUpRight, ChevronLeft, ChevronRight, Clock3, LayoutDashboard, LogOut, RefreshCw, Send, ShieldCheck, WalletCards } from 'lucide-react'
import { authApi, paymentsApi } from './services/api'
import { StatusMark } from './components/reactbits/ReactBits'

const money = amount => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(Number(amount || 0))
const initials = value => value.split(/\s|@/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase() || 'SP'
const displayName = user => user?.email?.split('@')[0] || 'ShariPay user'
const paymentStatus = status => ({ COMPLETED: 'done', PENDING_RISK: 'pending', VERIFICATION_REQUIRED: 'pending', HELD: 'pending', REJECTED: 'failed', FAILED: 'failed', CANCELLED: 'failed' }[status] || 'pending')
const paymentCopy = status => ({
  COMPLETED: ['Payment completed.', 'The backend confirmed this payment was settled.'],
  PENDING_RISK: ['Payment is processing.', 'Risk assessment is still in progress.'],
  VERIFICATION_REQUIRED: ['Verification required.', 'The backend requires verification before this payment can settle.'],
  HELD: ['Payment is on hold.', 'The backend policy engine placed this payment on hold.'],
  REJECTED: ['Payment rejected.', 'The backend policy engine rejected this payment.'],
  FAILED: ['Payment failed.', 'The payment could not be completed.'],
  CANCELLED: ['Payment cancelled.', 'This payment was cancelled.'],
}[status] || ['Payment status', 'The backend returned an unrecognized payment state.'])

function Brand() { return <div className="brand"><i><ShieldCheck size={20} /></i><span>Shari<span>Pay</span></span></div> }

function Shell({ page, navigate, user, onLogout, children }) {
  const nav = [['dashboard', LayoutDashboard, 'Overview'], ['send', Send, 'Send payment'], ['transactions', Clock3, 'Transactions']]
  return <main className="app-shell"><aside><Brand /><nav>{nav.map(([id, Icon, label]) => <button key={id} onClick={() => navigate(id)} className={page === id ? 'active' : ''}><Icon size={19} />{label}</button>)}</nav><div className="side-bottom"><button onClick={onLogout}><LogOut size={18} />Sign out</button><div className="profile"><div className="avatar">{initials(displayName(user))}</div><span><b>{displayName(user)}</b><small>Personal account</small></span></div></div></aside><section className="workspace">{children}</section></main>
}

function Top({ title, subtitle, user }) { return <header className="top"><div><h1>{title}</h1><p>{subtitle}</p></div><div><div className="avatar">{initials(displayName(user))}</div></div></header> }

function Login({ onAuthenticated }) {
  const [register, setRegister] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async event => {
    event.preventDefault(); setError(''); setBusy(true)
    try { onAuthenticated(await (register ? authApi.register(email, password) : authApi.login(email, password))) } catch (err) { setError(err.message || 'Unable to authenticate') } finally { setBusy(false) }
  }
  return <main className="login-page"><header><Brand /><span>India’s trusted real-time payments layer</span></header><div className="login-grid"><section className="login-copy"><p className="eyebrow">BUILT FOR INSTANT CONFIDENCE</p><h1>Money moves.<br />Trust stays.</h1><p>One intelligent payment layer for every transfer — verified before it settles.</p></section><section className="auth-card"><form className="auth-inner" onSubmit={submit}><div className="auth-title"><p className="eyebrow">{register ? 'CREATE YOUR ACCOUNT' : 'WELCOME BACK'}</p><h1>{register ? 'Start paying with confidence.' : 'Your payments, protected.'}</h1><p>{register ? 'Use a strong password: upper case, lower case, and a number.' : 'Sign in to your ShariPay account.'}</p></div><label>Email address<input required value={email} onChange={e => setEmail(e.target.value)} type="email" /></label><label>Password<input required minLength="8" value={password} onChange={e => setPassword(e.target.value)} type="password" /></label>{error && <p className="error-message" role="alert">{error}</p>}<button disabled={busy} className="primary wide" type="submit">{busy ? 'Please wait…' : register ? 'Create secure account' : 'Sign in securely'} <ArrowUpRight size={16} /></button><p className="switch">{register ? 'Already using ShariPay?' : 'New to ShariPay?'} <button type="button" onClick={() => { setRegister(!register); setError('') }}>{register ? 'Sign in' : 'Create account'}</button></p></form></section></div></main>
}

function TransactionList({ items, user, onSelect }) {
  if (!items.length) return <div className="transactions"><p className="empty-state">No payments yet.</p></div>
  return <div className="transactions">{items.map(t => { const sent = t.sender_user_id === user.id; const status = paymentStatus(t.status); return <button className="transaction" key={t.transaction_id} onClick={() => onSelect?.(t.transaction_id)}><div className={`avatar tx ${sent ? 'sent' : 'received'}`}>{sent ? '↑' : '↓'}</div><div className="tx-name"><b>{sent ? 'Payment sent' : 'Payment received'}</b><small>{t.transaction_id} · {new Date(t.created_at).toLocaleString('en-IN')}</small></div><StatusMark status={status} label={t.status.replaceAll('_', ' ')} size={16} /><div className={`amount ${sent ? 'sent' : 'received'}`}>{sent ? '-' : '+'} {money(t.amount)}</div><ChevronRight className="chev" size={17} /></button> })}</div>
}

function Dashboard({ navigate, user, account, transactions, onLogout, onSelect }) {
  const accountStatus = account ? (account.is_active ? 'ACTIVE' : 'INACTIVE') : 'ACTIVE'
  const accountLabel = account?.account_type ? `${account.account_type} account` : 'SIMULATED account'
  return <Shell page="dashboard" navigate={navigate} user={user} onLogout={onLogout}><Top user={user} title={`Welcome, ${displayName(user)}.`} subtitle="Here’s your payment overview for today." /><div className="dashboard-grid"><div className="balance-card"><div className="balance-top"><span><WalletCards />Available balance</span></div><h1>{money(account?.balance)}</h1><div className="balance-bottom"><span>{accountStatus} · {accountLabel} · {account?.currency || 'INR'} · {account?.id?.slice(-6).toUpperCase() || '—'}</span><button onClick={() => navigate('send')}><Send size={16} />Send payment</button></div></div><div className="quick-card"><p className="eyebrow">QUICK ACTIONS</p><button onClick={() => navigate('send')}><span><Send /></span>Send<br />money</button><button onClick={() => navigate('transactions')}><span><Clock3 /></span>View<br />activity</button></div></div><section className="section-head"><div><h2>Recent activity</h2><p>Payments retrieved from your ShariPay account.</p></div><button className="link-btn" onClick={() => navigate('transactions')}>View all <ChevronRight size={16} /></button></section><TransactionList items={transactions.slice(0, 3)} user={user} onSelect={onSelect} /></Shell>
}

function SendPayment({ navigate, user, beneficiaries, onLogout, onCreate, onBeneficiaryAdded }) {
  const [selected, setSelected] = useState(beneficiaries[0])
  const [amount, setAmount] = useState('')
  const [error, setError] = useState('')
  const [beneficiaryEmail, setBeneficiaryEmail] = useState('')
  const [beneficiaryName, setBeneficiaryName] = useState('')
  const [beneficiaryError, setBeneficiaryError] = useState('')
  const [idempotencyKey, setIdempotencyKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [addingBeneficiary, setAddingBeneficiary] = useState(false)
  useEffect(() => setSelected(beneficiaries[0]), [beneficiaries])
  const submit = async event => { event.preventDefault(); if (!selected) return; setError(''); setBusy(true); const requestKey = idempotencyKey || crypto.randomUUID(); setIdempotencyKey(requestKey); try { await onCreate(await paymentsApi.create({ beneficiary_id: selected.id, amount, currency: 'INR', idempotency_key: requestKey })); setIdempotencyKey('') } catch (err) { setError(err.status ? err.message : 'The payment response was not confirmed. Check transaction history before retrying.') } finally { setBusy(false) } }
  const addBeneficiary = async event => { event.preventDefault(); setBeneficiaryError(''); setAddingBeneficiary(true); try { await paymentsApi.createBeneficiary({ email: beneficiaryEmail, ...(beneficiaryName.trim() ? { display_name: beneficiaryName.trim() } : {}) }); setBeneficiaryEmail(''); setBeneficiaryName(''); await onBeneficiaryAdded() } catch (err) { setBeneficiaryError(err.message || 'Unable to add beneficiary') } finally { setAddingBeneficiary(false) } }
  return <Shell page="send" navigate={navigate} user={user} onLogout={onLogout}><Top user={user} title="Send payment" subtitle="Every transfer is verified before it reaches its destination." /><div className="send-layout"><section><form className="form-card" onSubmit={addBeneficiary}><p className="eyebrow">ADD A BENEFICIARY</p><label className="field-label">Email<input required type="email" value={beneficiaryEmail} onChange={e => setBeneficiaryEmail(e.target.value)} placeholder="recipient@example.com" /></label><label className="field-label">Display name<input value={beneficiaryName} onChange={e => setBeneficiaryName(e.target.value)} placeholder="Optional" /></label>{beneficiaryError && <p className="error-message" role="alert">{beneficiaryError}</p>}<button disabled={!beneficiaryEmail || addingBeneficiary} className="ghost wide" type="submit">{addingBeneficiary ? 'Adding beneficiary…' : 'Add beneficiary'}</button></form><form className="form-card" onSubmit={submit}><p className="eyebrow">CHOOSE A BENEFICIARY</p>{beneficiaries.length ? <div className="beneficiaries">{beneficiaries.map(b => <button type="button" key={b.id} onClick={() => setSelected(b)} className={selected?.id === b.id ? 'picked' : ''}><div className="avatar violet">{initials(b.display_name)}</div><span><b>{b.display_name}</b><small>{b.beneficiary_reference}</small></span>{selected?.id === b.id && <ShieldCheck size={18} />}</button>)}</div> : <p className="empty-state">Add a registered ShariPay user above to begin a payment.</p>}<label className="field-label">Amount <div className="amount-input"><span>₹</span><input required min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} type="number" placeholder="0.00" /></div></label>{error && <p className="error-message" role="alert">{error}</p>}<button disabled={!amount || !selected || busy} className="primary wide" type="submit">{busy ? 'Verifying payment…' : 'Confirm payment'} <ArrowUpRight size={17} /></button></form></section><aside className="safety-card"><div className="safety-icon"><ShieldCheck /></div><h3>Protected by ShariPay</h3><p>Payment creation invokes the backend risk and policy engine.</p></aside></div></Shell>
}

function PaymentStatus({ navigate, user, payment, onLogout }) { const status = paymentStatus(payment.status); const [title, message] = paymentCopy(payment.status); return <Shell page="send" navigate={navigate} user={user} onLogout={onLogout}><div className="status-page"><div className="success-icon"><StatusMark status={status} size={78} /></div><p className="eyebrow">PAYMENT {payment.status.replaceAll('_', ' ')}</p><h1>{title}</h1><p>{message}</p><div className="receipt"><div><span>Amount</span><b>{money(payment.amount)}</b></div><div><span>Reference ID</span><b>{payment.transaction_id}</b></div><div><span>Status</span><b>{payment.status.replaceAll('_', ' ')}</b></div></div>{payment.risk_assessment && <div className="receipt"><div><span>Risk</span><b>{payment.risk_assessment.risk_level} · {Number(payment.risk_assessment.risk_score).toFixed(2)}</b></div>{payment.policy_result && <div><span>Policy</span><b>{payment.policy_result.decision}</b></div>}</div>}<div className="status-actions"><button className="ghost" onClick={() => navigate('transactions')}>View transactions</button><button className="primary" onClick={() => navigate('dashboard')}>Back to overview</button></div></div></Shell> }

function PaymentDetails({ navigate, user, transactionId, onLogout }) {
  const [payment, setPayment] = useState(null)
  const [ledger, setLedger] = useState(null)
  const [ledgerHistory, setLedgerHistory] = useState([])
  const [error, setError] = useState('')
  const [ledgerMessage, setLedgerMessage] = useState('')
  useEffect(() => {
    let active = true
    setPayment(null); setLedger(null); setLedgerHistory([]); setError(''); setLedgerMessage('')
    Promise.all([
      paymentsApi.getPayment(transactionId),
      paymentsApi.getDrunixPayment(transactionId).catch(err => ({ error: err.message })),
      paymentsApi.getDrunixHistory(transactionId).catch(err => ({ error: err.message })),
    ]).then(([paymentResult, ledgerResult, historyResult]) => {
      if (!active) return
      setPayment(paymentResult)
      if (ledgerResult?.error) setLedgerMessage(ledgerResult.error); else setLedger(ledgerResult)
      if (historyResult?.error) setLedgerMessage(historyResult.error); else setLedgerHistory(historyResult.history || [])
    }).catch(err => active && setError(err.message || 'Unable to load payment details'))
    return () => { active = false }
  }, [transactionId])
  if (error) return <Shell page="transactions" navigate={navigate} user={user} onLogout={onLogout}><button className="ghost" onClick={() => navigate('transactions')}><ChevronLeft size={16} />Back to transactions</button><p className="error-message" role="alert">{error}</p></Shell>
  if (!payment) return <Shell page="transactions" navigate={navigate} user={user} onLogout={onLogout}><p>Loading payment details…</p></Shell>
  const [title, message] = paymentCopy(payment.status)
  return <Shell page="transactions" navigate={navigate} user={user} onLogout={onLogout}><button className="ghost" onClick={() => navigate('transactions')}><ChevronLeft size={16} />Back to transactions</button><div className="status-page"><p className="eyebrow">PAYMENT DETAILS</p><h1>{title}</h1><p>{message}</p><div className="receipt"><div><span>Reference ID</span><b>{payment.transaction_id}</b></div><div><span>Amount</span><b>{money(payment.amount)}</b></div><div><span>Status</span><b>{payment.status.replaceAll('_', ' ')}</b></div><div><span>Created</span><b>{new Date(payment.created_at).toLocaleString('en-IN')}</b></div></div>{payment.risk_assessment && <div className="receipt"><div><span>Risk</span><b>{payment.risk_assessment.risk_level} · {Number(payment.risk_assessment.risk_score).toFixed(2)}</b></div><div><span>Factors</span><b>{payment.risk_assessment.risk_factors?.join(', ') || 'No additional factors'}</b></div>{payment.policy_result && <div><span>Policy decision</span><b>{payment.policy_result.decision}</b></div>}</div>}<section className="form-card"><p className="eyebrow">LEDGER STATUS</p>{ledger ? <><div className="receipt"><div><span>Ledger status</span><b>{ledger.status || 'Confirmed'}</b></div><div><span>Ledger transaction</span><b>{ledger.transactionId || payment.transaction_id}</b></div></div>{ledgerHistory.length > 0 && <div className="transactions">{ledgerHistory.map(entry => <div className="transaction" key={`${entry.sequence}-${entry.timestamp}`}><div className="tx-name"><b>{entry.status}</b><small>{new Date(entry.timestamp).toLocaleString('en-IN')}</small></div><span>Step {entry.sequence}</span></div>)}</div>}</> : <p className="empty-state">{ledgerMessage || 'Ledger details are unavailable.'}</p>}</section></div></Shell>
}

function Transactions({ navigate, user, transactions, onLogout, onSelect, onRefresh }) { return <Shell page="transactions" navigate={navigate} user={user} onLogout={onLogout}><Top user={user} title="Transaction history" subtitle="A clear record of every payment from the backend." /><button className="link-btn" onClick={onRefresh}><RefreshCw size={16} />Refresh</button><TransactionList items={transactions} user={user} onSelect={onSelect} /></Shell> }

export default function App() {
  const [page, setPage] = useState('login'), [user, setUser] = useState(null), [account, setAccount] = useState(null), [beneficiaries, setBeneficiaries] = useState([]), [transactions, setTransactions] = useState([]), [payment, setPayment] = useState(null), [detailId, setDetailId] = useState(null), [loading, setLoading] = useState(authApi.isAuthenticated()), [error, setError] = useState('')
  const loadData = async () => { const [accounts, beneficiaryData, paymentData] = await Promise.all([paymentsApi.getAccounts(), paymentsApi.getBeneficiaries(), paymentsApi.getTransactions()]); setAccount(accounts[0] || null); setBeneficiaries(beneficiaryData); setTransactions(paymentData) }
  useEffect(() => { if (!authApi.isAuthenticated()) return; Promise.all([authApi.me(), loadData()]).then(([currentUser]) => { setUser(currentUser); setPage('dashboard') }).catch(async err => { await authApi.logout(); setUser(null); setPage('login'); setError('') }).finally(() => setLoading(false)) }, [])
  const authenticated = async currentUser => { setUser(currentUser); setLoading(true); try { await loadData(); setPage('dashboard') } catch (err) { setError(err.message) } finally { setLoading(false) } }
  const logout = async () => { await authApi.logout(); setUser(null); setPage('login') }
  const beneficiaryAdded = async () => { await loadData() }
  const created = async result => { setPayment(result); setPage('status'); try { await loadData() } catch {} }
  if (loading) return <main className="login-page"><p>Loading your secure account…</p></main>
  if (!user) return <Login onAuthenticated={authenticated} />
  if (error) return <main className="login-page"><p className="error-message">{error}</p><button className="primary" onClick={logout}>Return to sign in</button></main>
  const selectPayment = transactionId => { setDetailId(transactionId); setPage('detail') }
  const props = { navigate: setPage, user, onLogout: logout, onSelect: selectPayment, onRefresh: loadData }
  return <>{page === 'dashboard' && <Dashboard {...props} account={account} transactions={transactions} />}{page === 'send' && <SendPayment {...props} beneficiaries={beneficiaries} onCreate={created} onBeneficiaryAdded={beneficiaryAdded} />}{page === 'status' && payment && <PaymentStatus {...props} payment={payment} />}{page === 'transactions' && <Transactions {...props} transactions={transactions} />}{page === 'detail' && detailId && <PaymentDetails {...props} transactionId={detailId} />}</>
}
