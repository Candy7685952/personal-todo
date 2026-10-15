# 个人待办事项管理系统

第六周课程演示项目。使用 Streamlit 构建页面、SQLite 保存待办和分类，并支持新增、编辑、完成、删除及筛选。

## 在 Streamlit Community Cloud 部署

1. 将本文件夹中的内容上传到 GitHub 仓库根目录，保留 `.streamlit` 和 `data` 两个子目录。
2. 在 [Streamlit Community Cloud](https://share.streamlit.io/) 创建应用，选择该仓库及实际分支，主文件填写 `app.py`。
3. 部署完成后，将应用设置为公开，并在无痕窗口测试生成的链接。

`data/records.csv` 只包含四条虚构演示记录。首次运行时，应用会把它们导入 SQLite。Community Cloud 不保证本地 SQLite 文件长期保留，应用重启后，运行期间新增或修改的记录可能重置为这些演示记录。请勿在公开应用中填写真实个人信息。

## 本地运行

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```
