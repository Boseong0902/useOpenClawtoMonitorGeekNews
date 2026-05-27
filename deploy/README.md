# 배포 가이드 (Terraform + Ansible)

GeekNews RSS → Relay → OpenClaw → Slack 파이프라인을 AWS EC2에 자동 배포.

---

## 사전 준비

| 항목 | 상태 확인 |
|---|---|
| AWS CLI 설정 | `aws sts get-caller-identity` |
| Terraform >= 1.5 | `terraform --version` |
| Ansible >= 2.14 | `ansible --version` |
| SSH 키 페어 | `~/.ssh/slunch-backend-dev.pem` 존재 |
| Slack 앱 토큰 | `xapp-...`, `xoxb-...` 확보 |

---

## 1단계: Terraform — EC2 인스턴스 생성

```bash
cd deploy/terraform

# 설정 파일 생성
cp terraform.tfvars.example terraform.tfvars
vim terraform.tfvars  # key_name, my_ip_cidr 수정

# 실행
terraform init
terraform plan
terraform apply
```

완료 후 출력값 확인:

```bash
terraform output instance_public_ip   # → Ansible inventory에 사용
terraform output ssh_command           # → SSH 접속 명령어
```

---

## 2단계: Ansible — 서버 자동 구성

```bash
cd deploy/ansible

# inventory 설정
cp inventory.example inventory.ini
# ansible_host= 에 Terraform output IP 입력
vim inventory.ini

# 접속 테스트
ansible -i inventory.ini geeknews -m ping

# 전체 배포 (OpenClaw 설치 포함, 설정은 수동)
ansible-playbook -i inventory.ini playbook.yml \
  --extra-vars "relay_shared_secret=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
                openclaw_hook_token=$(python3 -c 'import secrets;print(secrets.token_hex(32))') \
                slack_channel_id=C0123456789"
```

Ansible이 완료하는 것:
- ✅ 시스템 패키지 (Python 3.11, Node.js 20, sqlite3)
- ✅ Ollama 설치 + `qwen2.5:7b` 모델 pull
- ✅ `geeknews` 시스템 유저 생성
- ✅ 프로젝트 clone + venv + pip install
- ✅ `.env` 배치 (시크릿 주입)
- ✅ relay systemd 서비스 등록 + 시작
- ✅ poller cron 등록
- ✅ OpenClaw npm 설치 + config 템플릿 배치

---

## 3단계: OpenClaw 수동 설정 (📸 캡처)

> **이 단계는 직접 SSH 접속해서 진행 — 과제 증빙 캡처용**

```bash
# SSH 접속
ssh -i ~/.ssh/slunch-backend-dev.pem ubuntu@<IP>

# geeknews 유저로 전환
sudo -u geeknews -i
cd /opt/geeknews
```

### 3-1. OpenClaw config 편집

```bash
vim openclaw.config.json
```

수정할 값:
- `channels.slack.appToken` → `xapp-...`
- `channels.slack.botToken` → `xoxb-...`
- `channels.slack.channels` → 실제 채널 ID
- `hooks.token` → `.env`의 `OPENCLAW_HOOK_TOKEN`과 동일 값

> **📸 설정 파일 편집 화면 캡처**

### 3-2. OpenClaw 서비스 등록

```bash
openclaw service install
sudo systemctl enable --now openclaw
sudo systemctl status openclaw
```

> **📸 `systemctl status openclaw` 캡처**

### 3-3. 연결 확인

```bash
curl -s http://127.0.0.1:18789/health
```

---

## 4단계: Slack 채널 설정 (📸 캡처)

1. Slack에서 `#assignment-geeknews` 채널 생성
2. OpenClaw 봇 초대 (`/invite @봇이름`)

> **📸 Slack 채널 + 봇 멤버십 캡처**

---

## 5단계: E2E 검증 (📸 캡처)

### 수동 1회 실행

```bash
sudo -u geeknews /opt/geeknews/poller/run.sh
```

### 로그 확인

```bash
# relay
tail -f /var/log/geeknews/relay.log | python3 -m json.tool

# poller
journalctl -u cron -f --grep geeknews

# openclaw
journalctl -u openclaw -f
```

### 기대 결과

1. poller → relay POST (새 글 감지)
2. relay → OpenClaw 호출 (에이전트 판단)
3. 매칭 시 → Slack 메시지 도착

> **📸 Slack 메시지 3건 이상 캡처**
>
> **📸 End-to-end 로그 한 사이클 캡처**

---

## 6단계: 재부팅 테스트

```bash
sudo reboot
# 재접속 후
sudo systemctl status relay openclaw
# 5분 내 poller cron 자동 실행 확인
```

---

## 트러블슈팅

| 증상 | 해결 |
|---|---|
| Terraform apply 실패 | `aws sts get-caller-identity`로 credentials 확인 |
| Ansible 접속 실패 | security group에 본인 IP 허용 여부, pem 파일 권한 `chmod 400` |
| relay 시작 실패 | `journalctl -u relay -e` — .env 경로 확인 |
| OpenClaw 502 | Ollama 동작 중인지: `curl http://127.0.0.1:11434/api/tags` |
| Slack 미전송 | `openclaw.config.json` 토큰/채널 ID 확인 |
| 한국어 품질 저하 | provider를 `anthropic`으로 전환 (아래 참조) |

---

## Ollama → Anthropic 전환 (필요시)

`/opt/geeknews/openclaw.config.json` 수정:

```json
{
  "llm": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "apiKey": "sk-ant-..."
  }
}
```

```bash
sudo systemctl restart openclaw
```

---

## 정리 (과제 제출 후)

```bash
cd deploy/terraform
terraform destroy
```
