import React, { useEffect, useState, useCallback } from 'react';
import { Megaphone, Plus, Trash2, Send, Edit3, Check, X, Loader2 } from 'lucide-react';
import api, { formatApiError } from '@/lib/api';

const emptyForm = () => ({ title: '', body: '', destination: '', audience: 'all', active: true });

/**
 * Admin-only section for authoring + broadcasting Feature Announcements.
 * Mounts inside AccountDashboard's admin card. Backend endpoints are all
 * under /api/admin/feature-announcements (protected by `_require_admin`).
 */
export default function AnnouncementComposerSection() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [form, setForm] = useState(emptyForm());
  const [editingId, setEditingId] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await api.get('/admin/feature-announcements');
      setItems(data?.items || []);
    } catch (e) {
      setError(formatApiError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const resetForm = () => { setForm(emptyForm()); setEditingId(null); };

  const submit = async (e) => {
    e.preventDefault();
    setError(''); setSuccess('');
    // Defence-in-depth: enforce the same length limits as the backend.
    if (!form.title.trim() || form.title.trim().length < 2) return setError('Title needs at least 2 characters.');
    if (form.title.trim().length > 80) return setError('Title max 80 characters.');
    if (!form.body.trim() || form.body.trim().length < 2) return setError('Body needs at least 2 characters.');
    if (form.body.trim().length > 180) return setError('Body max 180 characters.');
    setBusy(true);
    try {
      const payload = {
        title: form.title.trim(),
        body: form.body.trim(),
        destination: form.destination.trim() || null,
        audience: form.audience,
        active: !!form.active,
      };
      if (editingId) {
        await api.put(`/admin/feature-announcements/${editingId}`, payload);
        setSuccess('Announcement updated.');
      } else {
        await api.post('/admin/feature-announcements', payload);
        setSuccess('Announcement created — broadcast when ready.');
      }
      resetForm();
      await load();
    } catch (e) {
      setError(formatApiError(e));
    } finally { setBusy(false); }
  };

  const startEdit = (a) => {
    setForm({
      title: a.title || '', body: a.body || '',
      destination: a.destination || '', audience: a.audience || 'all',
      active: !!a.active,
    });
    setEditingId(a.id);
    setSuccess('');
    setError('');
  };

  const remove = async (id) => {
    if (!window.confirm('Delete this announcement? Users who already received it keep their copy.')) return;
    setBusy(true);
    try {
      await api.delete(`/admin/feature-announcements/${id}`);
      await load();
    } catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };

  const broadcast = async (id) => {
    setError(''); setSuccess(''); setBusy(true);
    try {
      const { data } = await api.post(`/admin/feature-announcements/${id}/broadcast`);
      setSuccess(`Delivered to ${data?.delivered || 0} eligible user(s).`);
    } catch (e) { setError(formatApiError(e)); }
    finally { setBusy(false); }
  };

  const charsTitle = form.title.length;
  const charsBody = form.body.length;

  return (
    <div className="border-t border-[#5C9E8C]/25 pt-6 mt-6" data-testid="announcement-composer-section">
      <div className="flex items-center gap-2 mb-4">
        <Megaphone size={14} className="text-[#C4A67A]" />
        <div className="label-tiny text-[#C4A67A]">Admin · Feature Announcements</div>
      </div>

      <form onSubmit={submit} className="grid grid-cols-1 gap-3 max-w-2xl">
        <div>
          <label className="label-tiny block mb-1">Title <span className="text-[#5A6B65]">({charsTitle}/80)</span></label>
          <input
            data-testid="ann-composer-title"
            type="text"
            maxLength={80}
            value={form.title}
            onChange={(e) => setForm({ ...form, title: e.target.value })}
            placeholder="A warm one-line headline"
            className="w-full bg-black/30 border border-[#5C9E8C]/30 rounded-md text-sm text-[#E8E3D9] px-3 py-2 focus:outline-none focus:border-[#72C2AC]"
          />
        </div>
        <div>
          <label className="label-tiny block mb-1">Body <span className="text-[#5A6B65]">({charsBody}/180)</span></label>
          <textarea
            data-testid="ann-composer-body"
            maxLength={180}
            rows={3}
            value={form.body}
            onChange={(e) => setForm({ ...form, body: e.target.value })}
            placeholder="A short, supportive description. No urgency. No clinical language."
            className="w-full bg-black/30 border border-[#5C9E8C]/30 rounded-md text-sm text-[#E8E3D9] px-3 py-2 focus:outline-none focus:border-[#72C2AC] resize-y"
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="label-tiny block mb-1">Destination path</label>
            <input
              data-testid="ann-composer-destination"
              type="text"
              maxLength={120}
              value={form.destination}
              onChange={(e) => setForm({ ...form, destination: e.target.value })}
              placeholder="/ or /play?frequency=528"
              className="w-full bg-black/30 border border-[#5C9E8C]/30 rounded-md text-sm text-[#E8E3D9] px-3 py-2 focus:outline-none focus:border-[#72C2AC]"
            />
          </div>
          <div>
            <label className="label-tiny block mb-1">Audience</label>
            <select
              data-testid="ann-composer-audience"
              value={form.audience}
              onChange={(e) => setForm({ ...form, audience: e.target.value })}
              className="w-full bg-black/30 border border-[#5C9E8C]/30 rounded-md text-sm text-[#E8E3D9] px-3 py-2"
            >
              <option value="all">Everyone</option>
              <option value="pro">Pro users only</option>
              <option value="free">Free users only</option>
            </select>
          </div>
          <div>
            <label className="label-tiny block mb-1">Status</label>
            <label className="flex items-center gap-2 mt-1 cursor-pointer">
              <input
                data-testid="ann-composer-active"
                type="checkbox"
                checked={!!form.active}
                onChange={(e) => setForm({ ...form, active: e.target.checked })}
                className="w-4 h-4 accent-[#5C9E8C]"
              />
              <span className="text-sm text-[#E8E3D9]">Active (eligible for delivery)</span>
            </label>
          </div>
        </div>
        <div className="flex items-center gap-2 mt-1">
          <button
            type="submit"
            data-testid="ann-composer-submit"
            disabled={busy}
            className="px-4 py-2 rounded-full bg-[#5C9E8C] hover:bg-[#72C2AC] text-[#08120F] font-medium text-sm transition-colors flex items-center gap-1.5 disabled:opacity-60"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : editingId ? <Check size={13} /> : <Plus size={13} />}
            {editingId ? 'Save changes' : 'Create announcement'}
          </button>
          {editingId && (
            <button
              type="button"
              data-testid="ann-composer-cancel-edit"
              onClick={resetForm}
              className="px-3 py-2 rounded-full border border-[#5C9E8C]/40 hover:border-[#F0B4A8]/70 text-[#C9DED6] text-xs transition-colors"
            >
              Cancel edit
            </button>
          )}
        </div>
        {error && <div className="text-xs text-[#F0B4A8] bg-[#F0B4A8]/10 border border-[#F0B4A8]/30 rounded-md px-3 py-2" data-testid="ann-composer-error">{error}</div>}
        {success && <div className="text-xs text-[#8ED8C1] bg-[#5C9E8C]/10 border border-[#5C9E8C]/30 rounded-md px-3 py-2" data-testid="ann-composer-success">{success}</div>}
      </form>

      <div className="mt-6">
        <div className="label-tiny mb-2 text-[#8A9A92]">Existing announcements</div>
        {loading && <div className="text-xs text-[#5A6B65]">Loading…</div>}
        {!loading && items.length === 0 && (
          <div className="text-xs text-[#5A6B65]" data-testid="ann-composer-empty">No announcements yet.</div>
        )}
        <div className="space-y-2 max-h-72 overflow-y-auto custom-scrollbar pr-1" data-testid="ann-composer-list">
          {items.map((a) => (
            <div
              key={a.id}
              data-testid={`ann-composer-row-${a.id}`}
              className="flex items-start justify-between gap-3 p-3 bg-black/25 border border-[#5C9E8C]/20 rounded-lg"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <div className="text-sm text-[#E8E3D9] leading-snug truncate">{a.title}</div>
                  {!a.active && (
                    <span className="text-[9px] uppercase tracking-[0.14em] text-[#5A6B65] border border-[#5A6B65]/50 rounded-full px-1.5 py-0.5">Inactive</span>
                  )}
                  <span className="text-[9px] uppercase tracking-[0.14em] text-[#72C2AC]/80 border border-[#72C2AC]/40 rounded-full px-1.5 py-0.5">{a.audience || 'all'}</span>
                </div>
                <div className="text-xs text-[#8A9A92] mt-1 line-clamp-2">{a.body}</div>
                {a.destination && (
                  <div className="text-[10px] uppercase tracking-[0.14em] text-[#5A6B65] mt-1">→ {a.destination}</div>
                )}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  data-testid={`ann-composer-broadcast-${a.id}`}
                  onClick={() => broadcast(a.id)}
                  disabled={busy || !a.active}
                  title={a.active ? 'Broadcast to all eligible users' : 'Activate first'}
                  className="p-1.5 rounded-md text-[#C4A67A] hover:text-[#E8B872] hover:bg-[#C4A67A]/15 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <Send size={13} />
                </button>
                <button
                  type="button"
                  data-testid={`ann-composer-edit-${a.id}`}
                  onClick={() => startEdit(a)}
                  title="Edit"
                  className="p-1.5 rounded-md text-[#8A9A92] hover:text-[#72C2AC] hover:bg-[#5C9E8C]/15 transition-colors"
                >
                  <Edit3 size={13} />
                </button>
                <button
                  type="button"
                  data-testid={`ann-composer-delete-${a.id}`}
                  onClick={() => remove(a.id)}
                  disabled={busy}
                  title="Delete"
                  className="p-1.5 rounded-md text-[#8A9A92] hover:text-[#F0B4A8] hover:bg-[#F0B4A8]/10 transition-colors disabled:opacity-40"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
