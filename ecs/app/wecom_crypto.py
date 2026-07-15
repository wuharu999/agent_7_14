"""
企业微信消息加解密 (WXBizMsgCrypt 实现)

用于：
1. GET /wecom/callback → URL 验证 (echostr 解密)
2. POST /wecom/callback → 消息体解密 + 签名验证
"""

import base64
import hashlib
import struct
import xml.etree.ElementTree as ET

try:
    from Cryptodome.Cipher import AES
except ImportError:
    from Crypto.Cipher import AES


class WXBizMsgCrypt:
    """企业微信加解密。"""

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id
        # 43 位 EncodingAESKey + "=" 拼成标准 base64 → 32 字节 AES 密钥
        key_bytes = base64.b64decode(encoding_aes_key + "=")
        if len(key_bytes) != 32:
            raise ValueError(f"AES 密钥长度错误: {len(key_bytes)} (应为 32)")
        self.aes_key = key_bytes

    # ── 签名 ────────────────────────────────────────

    def generate_signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        """SHA1(token, timestamp, nonce, encrypt) 排序后签名。"""
        sort_list = sorted([self.token, timestamp, nonce, encrypt])
        raw = "".join(sort_list)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    # ── 解密 ────────────────────────────────────────

    def _aes_cbc_decrypt(self, ciphertext_b64: str) -> bytes:
        """AES-256-CBC 解密 → 去掉 PKCS7 填充 → 返回明文。"""
        encrypted = base64.b64decode(ciphertext_b64)
        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        plain = cipher.decrypt(encrypted)
        # PKCS7 去掉填充
        pad = plain[-1]
        if pad < 1 or pad > 32:
            pad = 0
        return plain[: -pad] if pad else plain

    def _extract_xml(self, decrypted: bytes) -> str:
        """从解密结果中提取 XML 正文 (去掉 16 字节随机 + 4 字节长度)。"""
        msg_len = struct.unpack(">I", decrypted[16:20])[0]
        msg_bytes = decrypted[20 : 20 + msg_len]
        return msg_bytes.decode("utf-8")

    def decrypt(self, ciphertext_b64: str) -> str:
        """解密企业微信的加密文本，返回 XML 字符串。"""
        plain = self._aes_cbc_decrypt(ciphertext_b64)
        return self._extract_xml(plain)

    # ── 加密 ────────────────────────────────────────

    def encrypt(self, reply_xml: str, nonce: str, timestamp: str | None = None) -> str:
        """加密回复消息，返回 <xml><Encrypt>...</Encrypt><MsgSignature>...</MsgSignature>...</xml>。"""
        import time
        timestamp = timestamp or str(int(time.time()))

        rand = bytes(16)  # 16 字节随机（为简化全 0，生产建议 os.urandom）
        msg_bytes = reply_xml.encode("utf-8")
        raw = rand + struct.pack(">I", len(msg_bytes)) + msg_bytes + self.corp_id.encode("utf-8")

        # PKCS7 填充
        block_size = 32
        pad_len = block_size - (len(raw) % block_size)
        raw += bytes([pad_len] * pad_len)

        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(raw)
        encrypt_b64 = base64.b64encode(encrypted).decode("utf-8")

        sig = self.generate_signature(timestamp, nonce, encrypt_b64)

        xml = (
            f"<xml>"
            f"<Encrypt><![CDATA[{encrypt_b64}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{sig}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            f"</xml>"
        )
        return xml

    # ── 便捷解析 ────────────────────────────────────

    @staticmethod
    def parse_encrypt_xml(xml_str: str) -> dict:
        """解析企业微信回调的加密 XML，返回 {Encrypt, ToUserName, AgentID}。"""
        root = ET.fromstring(xml_str)
        return {
            "encrypt": root.findtext("Encrypt", ""),
            "to_user": root.findtext("ToUserName", ""),
            "agent_id": root.findtext("AgentID", ""),
        }

    @staticmethod
    def parse_decrypted_msg(xml_str: str) -> dict:
        """解析解密后的消息 XML，返回 {MsgType, Content, FromUserName, MsgId, CreateTime, ...}。"""
        root = ET.fromstring(xml_str)
        result = {}
        for child in root:
            result[child.tag] = child.text or ""
        return result
