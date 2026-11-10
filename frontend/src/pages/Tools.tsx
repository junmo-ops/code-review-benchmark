import { Card, Col, Row, Space, Typography } from 'antd';
import { FolderOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { Heading } from '../components/Common';

const tools = [
  {
    path: '/tools/repository-zip',
    name: '仓库 ZIP 工具',
    description: '上传仓库 ZIP，整理为最多两个分支，保留原提交，并按实际项目名生成可下载的 ZIP。',
    icon: <FolderOutlined />,
  },
];

export function Tools() {
  return <>
    <Heading title="工具箱" description="选择需要的工具，完成文件处理等辅助操作。" />
    <Row gutter={[16, 16]}>
      {tools.map((tool) => <Col xs={24} md={12} xl={8} key={tool.path}>
        <Card title={<Space>{tool.icon}{tool.name}</Space>}>
          <Typography.Paragraph>{tool.description}</Typography.Paragraph>
          <Link to={tool.path}>打开工具 →</Link>
        </Card>
      </Col>)}
    </Row>
  </>;
}
