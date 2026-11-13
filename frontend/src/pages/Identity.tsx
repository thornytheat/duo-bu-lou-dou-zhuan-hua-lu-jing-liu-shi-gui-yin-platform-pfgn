import { useEffect, useMemo, useRef, useState } from 'react';
import { identityApi } from '../api/identity';
import { useAsync } from '../hooks/useAsync';
import { useAuth } from '../router/guards';
import { uiStore } from '../stores/uiStore';
import { can } from '../constants/roles';
import { num, shortDate } from '../utils/format';
import { errorMessage } from '../utils/errorMessage';
import EmptyState from '../components/common/EmptyState';
import { IdentityPill } from '../components/common/StatusPill';
import type { IdentityLink, IdentityLinkStatus } from '../types/models';

const FILTERS: { value: string | undefined; label: string }[] = [
  { value: undefined, label: '全部' },
  { value: 'active', label: '生效中' },
  { value: 'suspended', label: '已中断' },
  { value: 'conflicted', label: '冲突' },
];

export default function Identity() {
  const { user } = useAuth();
  const role = user?.role;
  const canWrite = can(role, 'identity:write');
  const canResolve = can(role, 'identity:resolve');

  const [filter, setFilter] = useState<string | undefined>(undefined);
  const [anonymousKey, setAnonymousKey] = useState('');
  const [accountKey, setAccountKey] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const query = useMemo(() => ({ status: filter }), [filter]);
  const loader = useAsync(() => identityApi.list(query), [filter]);
  const conflictWarnedRef = useRef(false);

  useEffect(() => {
    const conflicted = loader.data?.summary?.conflicted ?? 0;
    if (conflicted > 0 && !conflictWarnedRef.current) {
      conflictWarnedRef.current = true;
      uiStore.toast(`有 ${conflicted} 条冲突关联待人工处理`, 'warn');
    }
  }, [loader.data?.summary?.conflicted]);

  const items = loader.data?.items ?? [];
  const summary = loader.data?.summary;

  async function deliver() {
    if (!anonymousKey.trim() || !accountKey.trim()) {
      setError('匿名标识与账号标识都要填写');
      return;
    }
    setBusy('deliver');
    setError(null);
    try {
      const result = await identityApi.deliver({
        anonymous_key: anonymousKey.trim(),
        account_key: accountKey.trim(),
        note: note.trim(),
      });
      if (result.delivery.conflict) {
        uiStore.toast('检测到同一匿名标识指向多个账号，已标记冲突且不会合并', 'warn');
      } else if (result.delivery.created) {
        uiStore.toast('关联已建立，下一次运行漏斗将合成跨身份轨迹', 'ok');
      } else {
        uiStore.toast(`同一关联重复投递（累计 ${result.delivery.delivery_count} 次），人数不新增`, 'info');
      }
      setAnonymousKey('');
      setAccountKey('');
      setNote('');
      loader.reload();
    } catch (err) {
      setError(errorMessage(err, role));
    } finally {
      setBusy(null);
    }
  }

  async function act(action: 'suspend' | 'resume' | 'resolve', link: IdentityLink, payload?: unknown) {
    setBusy(`${action}-${link.id}`);
    setError(null);
    try {
      if (action === 'suspend') await identityApi.suspend(link.id);
      if (action === 'resume') await identityApi.resume(link.id);
      if (action === 'resolve') await identityApi.resolve(link.id, payload as 'activate' | 'suspend');
      uiStore.toast('台账已更新，历史运行结果继续可用', 'ok');
      loader.reload();
    } catch (err) {
      setError(errorMessage(err, role));
    } finally {
      setBusy(null);
    }
  }

  if (loader.error) {
    return (
      <div className="error-box">
        <span>{errorMessage(loader.error, role)}</span>
        <button className="btn btn-sm" onClick={() => loader.reload()}>
          重试
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="panel line-a">
        <div className="panel-head" style={{ marginBottom: 10 }}>
          <div>
            <h2>身份关联台账</h2>
            <div className="tiny muted">
              匿名访客先看课后登录：关联生效后按行为事件时间合成一条路径，渠道与设备沿用最早有效触达
            </div>
          </div>
        </div>

        {summary ? (
          <div className="metric-row">
            <div className="metric">
              <span className="label">关联总数</span>
              <b>{num(summary.total)}</b>
              <small>去重后的关联对</small>
            </div>
            <div className="metric">
              <span className="label">生效中</span>
              <b>{num(summary.active)}</b>
              <small>参与身份合成</small>
            </div>
            <div className="metric">
              <span className="label">已中断</span>
              <b>{num(summary.suspended)}</b>
              <small>历史结果保留</small>
            </div>
            <div className="metric">
              <span className="label">冲突</span>
              <b style={summary.conflicted ? { color: 'var(--signal)' } : undefined}>{num(summary.conflicted)}</b>
              <small>禁止强行合并</small>
            </div>
            <div className="metric">
              <span className="label">重复投递</span>
              <b>{num(summary.repeated_deliveries)}</b>
              <small>人数不新增</small>
            </div>
          </div>
        ) : null}

        {canWrite ? (
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end', marginTop: 14 }}>
            <div className="field" style={{ marginBottom: 0, width: 200 }}>
              <label htmlFor="anon-key">匿名标识（anon: 开头）</label>
              <input
                id="anon-key"
                className="input mono"
                value={anonymousKey}
                onChange={(event) => setAnonymousKey(event.target.value)}
                placeholder="anon:dev00131"
                data-testid="anon-key"
              />
            </div>
            <div className="field" style={{ marginBottom: 0, width: 160 }}>
              <label htmlFor="account-key">账号标识</label>
              <input
                id="account-key"
                className="input mono"
                value={accountKey}
                onChange={(event) => setAccountKey(event.target.value)}
                placeholder="U0131"
                data-testid="account-key"
              />
            </div>
            <div className="field" style={{ marginBottom: 0, width: 220 }}>
              <label htmlFor="link-note">备注（可选）</label>
              <input
                id="link-note"
                className="input"
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder="登录后绑定设备匿名标识"
              />
            </div>
            <button className="btn btn-primary" onClick={deliver} disabled={busy !== null} data-testid="deliver-link">
              {busy === 'deliver' ? '投递中 …' : '投递关联'}
            </button>
          </div>
        ) : (
          <div className="tiny muted" style={{ marginTop: 10 }}>
            运营角色只读：可以查看台账与冲突，不能投递或变更关联。
          </div>
        )}

        {error ? (
          <div className="error-box" style={{ marginTop: 12 }}>
            <span>{error}</span>
            <button className="btn btn-sm" onClick={() => setError(null)}>
              知道了
            </button>
          </div>
        ) : null}
      </div>

      <div className="panel" style={{ marginTop: 20 }}>
        <div className="panel-head">
          <h3>关联明细</h3>
          <div className="btn-row">
            {FILTERS.map((item) => (
              <button
                key={item.label}
                className={`btn btn-sm ${filter === item.value ? 'btn-primary' : ''}`}
                onClick={() => setFilter(item.value)}
                data-testid={`filter-${item.value ?? 'all'}`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {!items.length ? (
          <EmptyState eyebrow="NO LINK" title="当前筛选下没有关联" hint="投递一条匿名标识与账号的绑定，或切换上方状态筛选。" />
        ) : (
          <table className="table" data-testid="identity-table">
            <thead>
              <tr>
                <th>状态</th>
                <th>匿名标识</th>
                <th>账号标识</th>
                <th className="num-cell">投递次数</th>
                <th>冲突说明</th>
                <th>最近投递</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((link) => (
                <tr key={link.id} data-testid={`link-row-${link.id}`}>
                  <td>
                    <IdentityPill status={link.status as IdentityLinkStatus} />
                  </td>
                  <td className="mono tiny">{link.anonymous_key}</td>
                  <td className="mono tiny">{link.account_key}</td>
                  <td className="num-cell">{num(link.delivery_count)}</td>
                  <td className="tiny">
                    {link.status === 'conflicted' ? (
                      <>
                        另指向 <b className="mono">{link.conflict_with ?? '—'}</b>
                        <div className="muted">两边原结果保留，需人工解决</div>
                      </>
                    ) : (
                      link.note || '—'
                    )}
                  </td>
                  <td className="tiny muted">{shortDate(link.last_delivery_at)}</td>
                  <td>
                    <div className="btn-row">
                      {link.status === 'active' && canWrite ? (
                        <button
                          className="btn btn-sm"
                          onClick={() => act('suspend', link)}
                          disabled={busy !== null}
                          data-testid={`suspend-${link.id}`}
                        >
                          中断
                        </button>
                      ) : null}
                      {link.status === 'suspended' && canWrite ? (
                        <button
                          className="btn btn-sm btn-primary"
                          onClick={() => act('resume', link)}
                          disabled={busy !== null}
                          data-testid={`resume-${link.id}`}
                        >
                          恢复
                        </button>
                      ) : null}
                      {link.status === 'conflicted' && canResolve ? (
                        <>
                          <button
                            className="btn btn-sm btn-primary"
                            onClick={() => act('resolve', link, 'activate')}
                            disabled={busy !== null}
                            data-testid={`resolve-activate-${link.id}`}
                          >
                            解决并生效
                          </button>
                          <button
                            className="btn btn-sm"
                            onClick={() => act('resolve', link, 'suspend')}
                            disabled={busy !== null}
                            data-testid={`resolve-suspend-${link.id}`}
                          >
                            放弃绑定
                          </button>
                        </>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="tiny muted" style={{ marginTop: 12 }}>
          冲突规则：一个匿名标识若已指向两个账号，相关关联全部置「冲突」且不参与合成；中断的关联保留台账与历史结果，恢复后下一次运行重新合成。
        </div>
      </div>
    </div>
  );
}
