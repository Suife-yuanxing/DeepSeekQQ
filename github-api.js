// GitHub API 工具 - 供 Claude Code 调用
const https = require('https');

const TOKEN = process.env.GITHUB_TOKEN;
if (!TOKEN) { console.error('❌ 未设置 GITHUB_TOKEN 环境变量'); process.exit(1); }

function githubAPI(path) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: 'api.github.com',
      path,
      headers: {
        'Authorization': `Bearer ${TOKEN}`,
        'User-Agent': 'Claude-Code-GitHub-Tool',
        'Accept': 'application/vnd.github.v3+json'
      }
    };
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch (e) { resolve(data); }
      });
    });
    req.on('error', reject);
    req.end();
  });
}

async function main() {
  const [,, cmd, ...args] = process.argv;

  switch (cmd) {
    case 'repos': {
      const repos = await githubAPI('/user/repos?sort=updated&per_page=20');
      if (Array.isArray(repos)) {
        console.log(`\n📁 共 ${repos.length} 个仓库:\n`);
        repos.forEach(r => {
          console.log(`  📦 ${r.full_name}`);
          console.log(`     语言: ${r.language || 'N/A'} | Stars: ${r.stargazers_count} | Forks: ${r.forks_count}`);
          console.log(`     描述: ${r.description || '无'}`);
          console.log(`     链接: ${r.html_url}`);
          console.log();
        });
      }
      break;
    }

    case 'issues': {
      const repo = args[0];
      if (!repo) { console.log('用法: node github-api.js issues owner/repo'); break; }
      const issues = await githubAPI(`/repos/${repo}/issues?state=open&per_page=10`);
      if (Array.isArray(issues)) {
        console.log(`\n📋 ${repo} 开放 Issues (${issues.length}):\n`);
        issues.forEach(i => {
          console.log(`  #${i.number} ${i.title}`);
          console.log(`     作者: ${i.user.login} | 状态: ${i.state} | 评论: ${i.comments}`);
          console.log(`     链接: ${i.html_url}`);
          console.log();
        });
      }
      break;
    }

    case 'read': {
      const [repo, path] = args;
      if (!repo || !path) { console.log('用法: node github-api.js read owner/repo path/to/file'); break; }
      const file = await githubAPI(`/repos/${repo}/contents/${path}`);
      if (file.content) {
        console.log(Buffer.from(file.content, 'base64').toString('utf-8'));
      } else {
        console.log('文件未找到或无权限');
      }
      break;
    }

    case 'search': {
      const query = args.join(' ');
      if (!query) { console.log('用法: node github-api.js search <关键词>'); break; }
      const result = await githubAPI(`/search/repositories?q=${encodeURIComponent(query)}&per_page=10`);
      if (result.items) {
        console.log(`\n🔍 搜索 "${query}" 结果:\n`);
        result.items.forEach(r => {
          console.log(`  📦 ${r.full_name} ⭐${r.stargazers_count}`);
          console.log(`     ${r.description || '无描述'}`);
          console.log();
        });
      }
      break;
    }

    case 'pr': {
      const repo = args[0];
      if (!repo) { console.log('用法: node github-api.js pr owner/repo'); break; }
      const prs = await githubAPI(`/repos/${repo}/pulls?state=open&per_page=10`);
      if (Array.isArray(prs)) {
        console.log(`\n🔀 ${repo} 开放 PRs (${prs.length}):\n`);
        prs.forEach(p => {
          console.log(`  #${p.number} ${p.title}`);
          console.log(`     作者: ${p.user.login} | 分支: ${p.head.ref} → ${p.base.ref}`);
          console.log(`     链接: ${p.html_url}`);
          console.log();
        });
      }
      break;
    }

    case 'profile': {
      const user = args[0] || '';
      const endpoint = user ? `/users/${user}` : '/user';
      const profile = await githubAPI(endpoint);
      console.log(`\n👤 GitHub 用户信息:\n`);
      console.log(`  用户名: ${profile.login}`);
      console.log(`  名称: ${profile.name || '未设置'}`);
      console.log(`  Bio: ${profile.bio || '未设置'}`);
      console.log(`  仓库数: ${profile.public_repos}`);
      console.log(`  Followers: ${profile.followers} | Following: ${profile.following}`);
      console.log(`  链接: ${profile.html_url}`);
      console.log();
      break;
    }

    default:
      console.log(`
🔧 GitHub API 工具

用法:
  node github-api.js repos                    - 列出你的仓库
  node github-api.js issues owner/repo        - 查看 Issues
  node github-api.js pr owner/repo            - 查看 PRs
  node github-api.js read owner/repo path     - 读取文件
  node github-api.js search <关键词>           - 搜索仓库
  node github-api.js profile [username]       - 查看用户信息
      `);
  }
}

main().catch(console.error);
