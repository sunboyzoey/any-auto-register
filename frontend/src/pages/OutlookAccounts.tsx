import { useEffect, useState, useCallback } from 'react'
import {
  Table,
  Button,
  Input,
  Select,
  Tag,
  Space,
  Modal,
  Form,
  Card,
  Popconfirm,
  Dropdown,
  Statistic,
  Row,
  Col,
  App,
  Tooltip,
  Switch,
  theme,
} from 'antd'
import PageHeader from '@/components/PageHeader'
import type { MenuProps } from 'antd'
import {
  ReloadOutlined,
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  DownloadOutlined,
  SearchOutlined,
  MailOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CopyOutlined,
  MoreOutlined,
  ExclamationCircleOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'

const MAIL_ACCESS_COLORS: Record<string, string> = {
  graph: 'success',
  imap_pop: 'warning',
  '': 'default',
}

const MAIL_ACCESS_LABELS: Record<string, string> = {
  graph: 'Graph API',
  imap_pop: 'IMAP/POP',
  '': '未检测',
}

const REGISTER_STATUS_COLORS: Record<string, string> = {
  '未注册': 'default',
  '进行中': 'processing',
  '已注册': 'success',
}

interface OutlookAccount {
  id: number
  email: string
  password: string
  client_id: string
  refresh_token: string
  mail_access_type: string
  mail_access_type_label: string
  mail_access_type_color: string
  gpt_register_status: string
  gpt_register_status_label: string
  gpt_register_status_color: string
  grok_register_status: string
  grok_register_status_label: string
  grok_register_status_color: string
  has_oauth: boolean
  enabled: boolean
  created_at: string
  updated_at: string
  last_used: string
}

interface Stats {
  total: number
  enabled: number
  disabled: number
  mail_access_type: { graph: number; imap_pop: number; unknown: number }
  gpt: { registered: number; unregistered: number; in_progress: number }
  grok: { registered: number; unregistered: number; in_progress: number }
}

export default function OutlookAccounts() {
  const { message: msg, modal } = App.useApp()
  const { token: themeToken } = theme.useToken()

  // ── 状态 ──
  const [accounts, setAccounts] = useState<OutlookAccount[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([])
  const [probing, setProbing] = useState(false)

  // 筛选
  const [keyword, setKeyword] = useState('')
  const [filterMailType, setFilterMailType] = useState<string | undefined>(undefined)
  const [filterGptStatus, setFilterGptStatus] = useState<string | undefined>(undefined)
  const [filterGrokStatus, setFilterGrokStatus] = useState<string | undefined>(undefined)
  const [filterEnabled, setFilterEnabled] = useState<boolean | undefined>(undefined)

  // 弹窗
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importLoading, setImportLoading] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const [editRecord, setEditRecord] = useState<OutlookAccount | null>(null)
  const [editForm] = Form.useForm()

  // ── 数据加载 ──
  const loadStats = useCallback(async () => {
    try {
      const data = await apiFetch('/outlook/accounts/stats')
      setStats(data)
    } catch { /* ignore */ }
  }, [])

  const loadAccounts = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      params.set('page', String(page))
      params.set('page_size', String(pageSize))
      if (keyword) params.set('keyword', keyword)
      if (filterMailType !== undefined) params.set('mail_access_type', filterMailType)
      if (filterGptStatus !== undefined) params.set('gpt_register_status', filterGptStatus)
      if (filterGrokStatus !== undefined) params.set('grok_register_status', filterGrokStatus)
      if (filterEnabled !== undefined) params.set('enabled', String(filterEnabled))

      const data = await apiFetch(`/outlook/accounts?${params.toString()}`)
      setAccounts(data.items || [])
      setTotal(data.total || 0)
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, keyword, filterMailType, filterGptStatus, filterGrokStatus, filterEnabled])

  useEffect(() => { loadAccounts() }, [loadAccounts])
  useEffect(() => { loadStats() }, [loadStats])

  const reload = () => {
    loadAccounts()
    loadStats()
    setSelectedRowKeys([])
  }

  // ── 操作 ──
  const handleDelete = async (id: number) => {
    await apiFetch(`/outlook/accounts/${id}`, { method: 'DELETE' })
    msg.success('删除成功')
    reload()
  }

  const handleBatchDelete = async () => {
    if (!selectedRowKeys.length) return
    await apiFetch('/outlook/accounts/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids: selectedRowKeys }),
    })
    msg.success(`已删除 ${selectedRowKeys.length} 条`)
    reload()
  }

  const handleBatchUpdateStatus = async (patch: Record<string, any>) => {
    if (!selectedRowKeys.length) return
    await apiFetch('/outlook/accounts/batch-update-status', {
      method: 'POST',
      body: JSON.stringify({ ids: selectedRowKeys, ...patch }),
    })
    msg.success('批量更新成功')
    reload()
  }

  const handleBatchProbe = async () => {
    if (!selectedRowKeys.length) return
    setProbing(true)
    try {
      const res = await apiFetch('/outlook/accounts/probe', {
        method: 'POST',
        body: JSON.stringify({ ids: selectedRowKeys }),
      })
      msg.success(`取码类型探测完成：成功 ${res.ok}，失败 ${res.failed}`)
      reload()
    } catch (e: any) {
      msg.error(`探测失败: ${e.message}`)
    } finally {
      setProbing(false)
    }
  }

  const handleDeleteAll = () => {
    modal.confirm({
      title: '确认清空',
      icon: <ExclamationCircleOutlined />,
      content: '将删除当前筛选条件下的所有 Outlook 邮箱账号，此操作不可撤销。',
      okText: '确认清空',
      okType: 'danger',
      onOk: async () => {
        const params = new URLSearchParams()
        if (filterMailType !== undefined) params.set('mail_access_type', filterMailType)
        if (filterGptStatus !== undefined) params.set('gpt_register_status', filterGptStatus)
        if (filterGrokStatus !== undefined) params.set('grok_register_status', filterGrokStatus)
        if (filterEnabled !== undefined) params.set('enabled', String(filterEnabled))
        const data = await apiFetch(`/outlook/accounts/delete-all?${params.toString()}`, { method: 'POST' })
        msg.success(`已清空 ${data.deleted} 条`)
        reload()
      },
    })
  }

  // 导入
  const handleImport = async () => {
    const text = importText.trim()
    if (!text) { msg.error('请输入导入内容'); return }
    setImportLoading(true)
    try {
      const res = await apiFetch('/outlook/batch-import', {
        method: 'POST',
        body: JSON.stringify({ data: text, enabled: true }),
      })
      const parts: string[] = []
      if (res.success) parts.push(`成功 ${res.success} 条`)
      if (res.graph_count) parts.push(`Graph ${res.graph_count}`)
      if (res.imap_pop_count) parts.push(`IMAP/POP ${res.imap_pop_count}`)
      if (res.deleted_bad) parts.push(`不可达已跳过 ${res.deleted_bad}`)
      if (res.failed) parts.push(`失败 ${res.failed}`)
      msg.success(`导入完成：${parts.join('，')}`)
      if (res.errors?.length) {
        Modal.warning({
          title: '部分导入异常',
          content: (
            <div style={{ maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap', fontFamily: 'monospace', fontSize: 12 }}>
              {res.errors.join('\n')}
            </div>
          ),
          width: 560,
        })
      }
      setImportOpen(false)
      setImportText('')
      reload()
    } catch (e: any) {
      msg.error(`导入失败: ${e.message}`)
    } finally {
      setImportLoading(false)
    }
  }

  // 导出
  const handleExport = async () => {
    const params = new URLSearchParams()
    if (filterMailType !== undefined) params.set('mail_access_type', filterMailType)
    if (filterGptStatus !== undefined) params.set('gpt_register_status', filterGptStatus)
    if (filterGrokStatus !== undefined) params.set('grok_register_status', filterGrokStatus)
    if (filterEnabled !== undefined) params.set('enabled', String(filterEnabled))
    const data = await apiFetch(`/outlook/accounts/export?${params.toString()}`)
    const blob = new Blob([data.data], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `outlook_accounts_${Date.now()}.txt`
    a.click()
    URL.revokeObjectURL(url)
    msg.success(`已导出 ${data.total} 条`)
  }

  // 编辑
  const openEdit = (record: OutlookAccount) => {
    setEditRecord(record)
    editForm.setFieldsValue({
      password: record.password,
      client_id: record.client_id,
      refresh_token: record.refresh_token,
      mail_access_type: record.mail_access_type || '',
      gpt_register_status: record.gpt_register_status || '未注册',
      grok_register_status: record.grok_register_status || '未注册',
      enabled: record.enabled,
    })
    setEditOpen(true)
  }

  const handleEditSave = async () => {
    if (!editRecord) return
    const values = await editForm.validateFields()
    await apiFetch(`/outlook/accounts/${editRecord.id}`, {
      method: 'PUT',
      body: JSON.stringify(values),
    })
    msg.success('更新成功')
    setEditOpen(false)
    reload()
  }

  // 复制
  const copyText = (text: string) => {
    navigator.clipboard.writeText(text).then(() => msg.success('已复制'))
  }

  // ── 批量操作下拉 ──
  const batchMenuItems: MenuProps['items'] = [
    {
      key: 'probe',
      label: '刷新取码类型',
      onClick: handleBatchProbe,
    },
    { type: 'divider' },
    {
      key: 'enable',
      label: '批量启用',
      onClick: () => handleBatchUpdateStatus({ enabled: true }),
    },
    {
      key: 'disable',
      label: '批量禁用',
      onClick: () => handleBatchUpdateStatus({ enabled: false }),
    },
    { type: 'divider' },
    {
      key: 'gpt_unregistered',
      label: 'GPT 状态 → 未注册',
      onClick: () => handleBatchUpdateStatus({ gpt_register_status: '未注册' }),
    },
    {
      key: 'gpt_registered',
      label: 'GPT 状态 → 已注册',
      onClick: () => handleBatchUpdateStatus({ gpt_register_status: '已注册' }),
    },
    { type: 'divider' },
    {
      key: 'grok_unregistered',
      label: 'Grok 状态 → 未注册',
      onClick: () => handleBatchUpdateStatus({ grok_register_status: '未注册' }),
    },
    {
      key: 'grok_registered',
      label: 'Grok 状态 → 已注册',
      onClick: () => handleBatchUpdateStatus({ grok_register_status: '已注册' }),
    },
    { type: 'divider' },
    {
      key: 'batch_delete',
      label: <span style={{ color: themeToken.colorError }}>批量删除</span>,
      onClick: () => {
        modal.confirm({
          title: '确认批量删除',
          content: `将删除选中的 ${selectedRowKeys.length} 条记录`,
          okType: 'danger',
          onOk: handleBatchDelete,
        })
      },
    },
  ]

  // ── 表格列 ──
  const columns: any[] = [
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      width: 260,
      ellipsis: true,
      render: (text: string) => (
        <Space size={4}>
          <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{text}</span>
          <Tooltip title="复制"><CopyOutlined style={{ cursor: 'pointer', opacity: 0.5, fontSize: 11 }} onClick={() => copyText(text)} /></Tooltip>
        </Space>
      ),
    },
    {
      title: '密码',
      dataIndex: 'password',
      key: 'password',
      width: 120,
      render: (text: string) => (
        <Tooltip title={text}>
          <span style={{ fontFamily: 'monospace', fontSize: 12, filter: 'blur(4px)', cursor: 'pointer' }}
            onMouseEnter={(e) => (e.currentTarget.style.filter = 'none')}
            onMouseLeave={(e) => (e.currentTarget.style.filter = 'blur(4px)')}
          >
            {text || '-'}
          </span>
        </Tooltip>
      ),
    },
    {
      title: '取码类型',
      dataIndex: 'mail_access_type',
      key: 'mail_access_type',
      width: 110,
      render: (type: string) => {
        const label = MAIL_ACCESS_LABELS[type] || type || '未检测'
        const color = MAIL_ACCESS_COLORS[type] || 'default'
        return <Tag color={color}>{label}</Tag>
      },
    },
    {
      title: 'OAuth',
      key: 'has_oauth',
      width: 70,
      align: 'center' as const,
      render: (_: any, record: OutlookAccount) =>
        record.has_oauth
          ? <CheckCircleOutlined style={{ color: themeToken.colorSuccess }} />
          : <CloseCircleOutlined style={{ color: themeToken.colorTextQuaternary }} />,
    },
    {
      title: 'GPT 注册',
      dataIndex: 'gpt_register_status',
      key: 'gpt_register_status',
      width: 100,
      render: (status: string) => (
        <Tag color={REGISTER_STATUS_COLORS[status] || 'default'}>{status || '未注册'}</Tag>
      ),
    },
    {
      title: 'Grok 注册',
      dataIndex: 'grok_register_status',
      key: 'grok_register_status',
      width: 100,
      render: (status: string) => (
        <Tag color={REGISTER_STATUS_COLORS[status] || 'default'}>{status || '未注册'}</Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 70,
      render: (enabled: boolean) => (
        <Tag color={enabled ? 'success' : 'error'}>
          {enabled ? '启用' : '禁用'}
        </Tag>
      ),
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 160,
      render: (text: string) => {
        if (!text) return '-'
        const d = new Date(text)
        return Number.isNaN(d.getTime()) ? text : d.toLocaleString()
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      fixed: 'right' as const,
      render: (_: any, record: OutlookAccount) => (
        <Space size={0}>
          <Button type="text" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)} />
          <Popconfirm title="确认删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <PageHeader
        title="Outlook 邮箱管理"
        subtitle="管理用于平台注册的 Outlook 邮箱账号池"
        icon={<MailOutlined />}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={reload}>刷新</Button>
            <Button icon={<DownloadOutlined />} onClick={handleExport}>导出</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setImportOpen(true)}>批量导入</Button>
          </Space>
        }
      />

      {/* ── 统计卡片 ── */}
      {stats && (
        <Row gutter={16}>
          <Col span={4}>
            <Card size="small">
              <Statistic title="总计" value={stats.total} valueStyle={{ fontSize: 20 }} />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic title="已启用" value={stats.enabled} valueStyle={{ color: themeToken.colorSuccess, fontSize: 20 }} />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic title="Graph API" value={stats.mail_access_type.graph} valueStyle={{ color: themeToken.colorSuccess, fontSize: 20 }} />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic title="IMAP/POP" value={stats.mail_access_type.imap_pop} valueStyle={{ color: themeToken.colorWarning, fontSize: 20 }} />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic title="GPT 已注册" value={stats.gpt.registered} valueStyle={{ color: themeToken.colorSuccess, fontSize: 20 }} />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic title="Grok 已注册" value={stats.grok.registered} valueStyle={{ color: themeToken.colorSuccess, fontSize: 20 }} />
            </Card>
          </Col>
        </Row>
      )}

      {/* ── 筛选栏 ── */}
      <Card size="small">
        <Space wrap>
          <Input
            placeholder="搜索邮箱"
            prefix={<SearchOutlined />}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={() => { setPage(1); loadAccounts() }}
            style={{ width: 220 }}
            allowClear
          />
          <Select
            placeholder="取码类型"
            value={filterMailType}
            onChange={(v) => { setFilterMailType(v); setPage(1) }}
            allowClear
            style={{ width: 140 }}
            options={[
              { label: 'Graph API', value: 'graph' },
              { label: 'IMAP/POP', value: 'imap_pop' },
              { label: '未检测', value: '' },
            ]}
          />
          <Select
            placeholder="GPT 状态"
            value={filterGptStatus}
            onChange={(v) => { setFilterGptStatus(v); setPage(1) }}
            allowClear
            style={{ width: 120 }}
            options={[
              { label: '未注册', value: '未注册' },
              { label: '进行中', value: '进行中' },
              { label: '已注册', value: '已注册' },
            ]}
          />
          <Select
            placeholder="Grok 状态"
            value={filterGrokStatus}
            onChange={(v) => { setFilterGrokStatus(v); setPage(1) }}
            allowClear
            style={{ width: 120 }}
            options={[
              { label: '未注册', value: '未注册' },
              { label: '进行中', value: '进行中' },
              { label: '已注册', value: '已注册' },
            ]}
          />
          <Select
            placeholder="启用状态"
            value={filterEnabled}
            onChange={(v) => { setFilterEnabled(v); setPage(1) }}
            allowClear
            style={{ width: 110 }}
            options={[
              { label: '启用', value: true },
              { label: '禁用', value: false },
            ]}
          />
        </Space>
      </Card>

      {/* ── 表格 ── */}
      <Card
        title={
          <Space>
            <span>邮箱列表</span>
            {selectedRowKeys.length > 0 && (
              <Tag color="blue">{selectedRowKeys.length} 项已选</Tag>
            )}
          </Space>
        }
        extra={
          <Space>
            {selectedRowKeys.length > 0 && (
              <Dropdown menu={{ items: batchMenuItems }} trigger={['click']} disabled={probing}>
                <Button icon={<MoreOutlined />} loading={probing}>
                  {probing ? '探测中...' : '批量操作'}
                </Button>
              </Dropdown>
            )}
            <Popconfirm
              title="确认清空当前筛选范围？"
              description="此操作不可撤销"
              onConfirm={handleDeleteAll}
              okType="danger"
            >
              <Button danger icon={<DeleteOutlined />}>清空</Button>
            </Popconfirm>
          </Space>
        }
      >
        <Table
          rowKey="id"
          columns={columns}
          dataSource={accounts}
          loading={loading}
          size="middle"
          scroll={{ x: 1200 }}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys as number[]),
          }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => { setPage(p); setPageSize(ps) },
          }}
        />
      </Card>

      {/* ── 批量导入弹窗 ── */}
      <Modal
        title="批量导入 Outlook 邮箱"
        open={importOpen}
        onCancel={() => { if (!importLoading) setImportOpen(false) }}
        onOk={handleImport}
        confirmLoading={importLoading}
        okText={importLoading ? '探测取码类型中...' : '导入'}
        width={680}
        maskClosable={false}
        closable={!importLoading}
      >
        <div style={{ marginBottom: 12, color: themeToken.colorTextSecondary, fontSize: 13 }}>
          <p style={{ margin: '4px 0' }}>每行一个账户，字段用 <code>----</code> 分隔，固定格式：</p>
          <p style={{ margin: '4px 0', fontWeight: 500, color: themeToken.colorText }}>
            <code>邮箱----密码----刷新令牌----Client ID</code>
          </p>
          <p style={{ margin: '8px 0 0', fontSize: 12, color: themeToken.colorTextTertiary }}>
            导入时会自动探测每个账号的取码类型（Graph API / IMAP / POP），不可达的账号将被跳过。探测过程可能需要一些时间，请耐心等待。
          </p>
        </div>
        <Input.TextArea
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          placeholder={`example@outlook.com----password----M.C552_BAY.0.U.-Cv...----9e5f94bc-e8a4-4e73-b8be-63364c29d753`}
          autoSize={{ minRows: 8, maxRows: 18 }}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
          disabled={importLoading}
        />
      </Modal>

      {/* ── 编辑弹窗 ── */}
      <Modal
        title={`编辑 - ${editRecord?.email || ''}`}
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleEditSave}
        maskClosable={false}
        width={560}
      >
        <Form form={editForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="password" label="密码">
            <Input.Password />
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="client_id" label="Client ID">
                <Input placeholder="OAuth Client ID" style={{ fontFamily: 'monospace', fontSize: 12 }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="mail_access_type" label="取码类型">
                <Select
                  options={[
                    { label: 'Graph API', value: 'graph' },
                    { label: 'IMAP/POP', value: 'imap_pop' },
                    { label: '未检测', value: '' },
                  ]}
                />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="refresh_token" label="Refresh Token">
            <Input.TextArea rows={2} style={{ fontFamily: 'monospace', fontSize: 11 }} />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="gpt_register_status" label="GPT 注册状态">
                <Select
                  options={[
                    { label: '未注册', value: '未注册' },
                    { label: '进行中', value: '进行中' },
                    { label: '已注册', value: '已注册' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="grok_register_status" label="Grok 注册状态">
                <Select
                  options={[
                    { label: '未注册', value: '未注册' },
                    { label: '进行中', value: '进行中' },
                    { label: '已注册', value: '已注册' },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="enabled" label="启用" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>
    </div>
  )
}
