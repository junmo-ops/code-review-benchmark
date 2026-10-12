import { Alert, Card, Space, Steps, Typography } from 'antd';
import { Heading } from '../components/Common';
export function Guide() {
  return (
    <>
      <Heading title="使用指南" description="从资源准备到运行、评分与结果保存。" />
      <Steps
        className="section-gap"
        items={[
          { title: '准备仓库与案例' },
          { title: '配置模型' },
          { title: '创建并开始' },
          { title: '阅读报告' },
        ]}
      />
      <Space direction="vertical" className="full-width">
        <Card title="仓库和案例集分开维护">
          <Typography.Paragraph>
            先在仓库管理上传含 .git 的仓库 ZIP，再在案例集管理上传配置
            ZIP。更换案例数量时复用已有仓库。补充包可修复缺失提交，无需重新上传完整仓库。
          </Typography.Paragraph>
        </Card>
        <Card title="单模型与多模型">
          <Typography.Paragraph>
            单模型测评支持分步检视、评分、恢复和归档。多模型实验使用相同案例、裁判与预算，逐组串行执行，单例失败后继续。结果对比读取已有评分，不重新调用模型。
          </Typography.Paragraph>
        </Card>
        <Card title="失败、重试与部分评分">
          <Typography.Paragraph>
            已完成的答卷和日志保留。评分只覆盖有效完整答卷，未完成案例排除并在报告中标明。恢复答卷不重新检视；允许重跑未完成案例和自动重试可能增加模型调用。
          </Typography.Paragraph>
        </Card>
        <Card title="专项测评">
          <Typography.Paragraph>
            MCR 按轮次回放，直接模型和 LCR 成绩分开。c-CRAB 运行检视、DeepSeek
            修复与容器发布测试，不使用 F1；每批次执行一次。
          </Typography.Paragraph>
        </Card>
        <Alert
          type="info"
          message="关闭浏览器不会停止后台任务；更新服务前请确认 COS 同步状态。模型配置的流式开关会固定到新批次。"
        />
      </Space>
    </>
  );
}
