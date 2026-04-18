import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Row, Col, Statistic, Progress, Tag, Button, Spin, Space, Divider, Typography, theme, Tooltip } from 'antd'
import PageHeader from '@/components/PageHeader'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
  MailOutlined,
  GlobalOutlined,
  SafetyOutlined,
  RocketOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'
import { PLATFORM_META } from '@/theme'

const { Text } = Typography

export default function Dashboard() {
  const navigate = useNavigate()
  const { token: t } = theme.useToken()
  const [stats, setStats] = useState<any>(null)
  const [outlookStats, setOutlookStats] = useState<any>(null)
  const [solverStatus, setSolverStatus] = useState<boolean | null>(null)
  const [cfworkerDomains, setCfworkerDomains] = useState<string[]>([])
  const [loading, setLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const [acc, outlook, solver, cfworker] = await Promise.all([
        apiFetch('/accounts/stats').catch(() => null),
        apiFetch('/outlook/accounts/stats').catch(() => null),
        apiFetch('/solver/status').catch(() => null),
        apiFetch('/config/cfworker/domains').catch(() => null),
      ])
      setStats(acc)
      setOutlookStats(outlook)
      setSolverStatus(solver?.running ?? null)
      setCfworkerDomains(cfworker?.domains || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const totalAccounts = stats?.total ?? 0
  const platforms = stats?.by_platform || {}
  const outlookTotal = outlookStats?.total ?? 0
  const outlookEnabled = outlookStats?.enabled ?? 0

  const gradient = (color: string, alpha = 0.12) =>
    `linear-gradient(135deg, ${color}${Math.round(alpha * 255).toString(16).padStart(2, '0')} 0%, transparent 100%)`

  return (
    <div className="page-transition">
      <PageHeader
        title="仪表盘"
        subtitle="系统总览 · 邮箱 · 域名 · 服务状态"
        extra={<Button icon={<ReloadOutlined spin={loading} />} onClick={load} loading={loading}>刷新</Button>}
      />

      {/* ═══════════ 第一行：3 个服务指标 ═══════════ */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={8}>
          <Card
            style={{ borderRadius: t.borderRadiusLG, height: '100%', background: gradient('#10b981'), border: 'none' }}
            hoverable onClick={() => navigate('/outlook')}
          >
            <Statistic
              title={<Space><MailOutlined />Outlook 邮箱池</Space>}
              value={outlookEnabled}
              suffix={<Text type="secondary" style={{ fontSize: 14 }}>/ {outlookTotal}</Text>}
              valueStyle={{ color: t.colorSuccess, fontSize: 32, fontWeight: 700 }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card style={{ borderRadius: t.borderRadiusLG, height: '100%', background: gradient('#1d9bf0'), border: 'none' }}>
            <Statistic
              title={<Space><GlobalOutlined />CF Worker 域名</Space>}
              value={cfworkerDomains.length}
              valueStyle={{ color: '#1d9bf0', fontSize: 32, fontWeight: 700 }}
            />
            {cfworkerDomains.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {cfworkerDomains.map(d => <Tag key={d} style={{ marginBottom: 4 }}>{d}</Tag>)}
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card style={{
            borderRadius: t.borderRadiusLG, height: '100%', border: 'none',
            background: gradient(solverStatus ? '#10b981' : '#ef4444'),
          }}>
            <Statistic
              title={<Space><SafetyOutlined />Turnstile Solver</Space>}
              value={solverStatus === null ? '检测中' : solverStatus ? '运行中' : '未运行'}
              valueStyle={{
                color: solverStatus ? t.colorSuccess : solverStatus === false ? t.colorError : t.colorTextSecondary,
                fontSize: 24, fontWeight: 700,
              }}
            />
            <div style={{ marginTop: 8 }}>
              <Tag
                color={solverStatus ? 'success' : solverStatus === false ? 'error' : 'default'}
                icon={solverStatus ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
              >
                {solverStatus ? '在线' : '离线'}
              </Tag>
            </div>
          </Card>
        </Col>
      </Row>

      {/* ═══════════ 第二行：平台账号分布（全宽） ═══════════ */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24}>
          <Card
            title={<Space><RocketOutlined />平台账号分布 <Tag>{totalAccounts} 个账号</Tag></Space>}
            style={{ borderRadius: t.borderRadiusLG }}
          >
            {loading ? (
              <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
            ) : (
              <Row gutter={[16, 16]}>
                {Object.entries(PLATFORM_META).map(([key, meta]) => {
                  const count = platforms[key] || 0
                  const pct = totalAccounts ? Math.round((count / totalAccounts) * 100) : 0
                  return (
                    <Col xs={12} sm={8} md={6} lg={4} key={key}>
                      <div
                        style={{
                          padding: '20px 16px', borderRadius: t.borderRadiusLG,
                          background: gradient(meta.color, 0.06),
                          border: `1px solid ${t.colorBorderSecondary}`,
                          cursor: 'pointer', transition: 'all 0.25s ease',
                          textAlign: 'center',
                        }}
                        onClick={() => navigate(`/accounts/${key}`)}
                        onMouseEnter={e => {
                          e.currentTarget.style.borderColor = meta.color
                          e.currentTarget.style.transform = 'translateY(-2px)'
                          e.currentTarget.style.boxShadow = `0 6px 20px ${meta.color}20`
                        }}
                        onMouseLeave={e => {
                          e.currentTarget.style.borderColor = t.colorBorderSecondary
                          e.currentTarget.style.transform = 'none'
                          e.currentTarget.style.boxShadow = 'none'
                        }}
                      >
                        <div style={{ fontSize: 28, marginBottom: 4 }}>{meta.icon}</div>
                        <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>{meta.label}</div>
                        <div style={{ fontSize: 28, fontWeight: 700, color: meta.color, marginBottom: 8 }}>{count}</div>
                        <Progress percent={pct} strokeColor={meta.color} size="small" showInfo={false} />
                      </div>
                    </Col>
                  )
                })}
              </Row>
            )}
          </Card>
        </Col>
      </Row>

      {/* ═══════════ 第三行：Outlook 邮箱池概览 ═══════════ */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24}>
          <Card
            title={<Space><MailOutlined />Outlook 邮箱池</Space>}
            extra={<Button type="link" onClick={() => navigate('/outlook')}>管理 →</Button>}
            style={{ borderRadius: t.borderRadiusLG }}
          >
            <Row gutter={[24, 16]} align="middle">
              <Col xs={12} sm={6}>
                <Statistic title="总计" value={outlookTotal} valueStyle={{ fontSize: 22 }} />
              </Col>
              <Col xs={12} sm={6}>
                <Statistic title="可用" value={outlookEnabled} valueStyle={{ color: t.colorSuccess, fontSize: 22 }} />
              </Col>
              <Col xs={12} sm={6}>
                <Statistic title="Graph API" value={outlookStats?.mail_access_type?.graph ?? 0} valueStyle={{ color: t.colorSuccess, fontSize: 22 }} />
              </Col>
              <Col xs={12} sm={6}>
                <Statistic title="IMAP/POP" value={outlookStats?.mail_access_type?.imap_pop ?? 0} valueStyle={{ color: t.colorWarning, fontSize: 22 }} />
              </Col>
              <Col xs={24}>
                <Divider style={{ margin: '8px 0' }} />
                <Row gutter={16}>
                  {Object.entries(PLATFORM_META).map(([key, meta]) => {
                    const fieldMap: Record<string, string> = {
                      chatgpt: 'gpt', grok: 'grok', trae: 'trae', kiro: 'kiro',
                      openblocklabs: 'obl', cursor: 'cursor',
                    }
                    const field = fieldMap[key]
                    const registered = field ? (outlookStats?.[field]?.registered ?? 0) : 0
                    const unregistered = field ? (outlookStats?.[field]?.unregistered ?? 0) : 0
                    if (!field) return null
                    return (
                      <Col xs={12} sm={4} key={key}>
                        <Tooltip title={`已注册 ${registered} / 未注册 ${unregistered}`}>
                          <div style={{ textAlign: 'center' }}>
                            <div style={{ fontSize: 12, color: t.colorTextSecondary, marginBottom: 4 }}>
                              {meta.icon} {meta.label}
                            </div>
                            <span style={{ fontWeight: 600, color: registered > 0 ? t.colorSuccess : t.colorTextSecondary }}>
                              {registered}
                            </span>
                            <span style={{ color: t.colorTextQuaternary }}> / {unregistered}</span>
                          </div>
                        </Tooltip>
                      </Col>
                    )
                  })}
                </Row>
              </Col>
            </Row>
          </Card>
        </Col>
      </Row>
    </div>
  )
}
