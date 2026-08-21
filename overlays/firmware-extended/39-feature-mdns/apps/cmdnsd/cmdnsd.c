/* Minimal mDNS A responder. Answers <hostname>.local queries only.
 *
 * Uses recvmsg()+IP_PKTINFO to auto-detect the reply address per packet,
 * and a compression-pointer-aware DNS name parser (RFC 1035 section
 * 4.1.4) so it correctly answers multi-question queries that use name
 * compression, which real-world mDNS clients (e.g. macOS) send. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <ctype.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/in.h>

#define MDNS_ADDR "224.0.0.251"
#define MDNS_PORT 5353
#define MAX_NAME 256
#define MAX_PACKET 9036

static char g_target[MAX_NAME]; /* "<hostname>.local", lowercase */

/* Parse a DNS name at buf[offset], following compression pointers.
 * Writes lowercase dotted name into out (size outsz). Returns the
 * stream offset just past the name in the ORIGINAL packet (i.e. after
 * the first pointer/terminator encountered), or -1 on error. */
static int parse_name(const unsigned char *buf, int buflen, int offset, char *out, int outsz)
{
	int pos = offset;
	int first_ret = -1;
	int outlen = 0;
	int visited[64];
	int nvisited = 0;

	out[0] = 0;

	for (;;) {
		if (pos < 0 || pos >= buflen)
			return -1;

		for (int i = 0; i < nvisited; i++) {
			if (visited[i] == pos)
				return -1; /* loop */
		}
		if (nvisited < 64)
			visited[nvisited++] = pos;

		int len = buf[pos];

		if (len == 0) {
			pos += 1;
			break;
		}

		if ((len & 0xC0) == 0xC0) {
			if (pos + 1 >= buflen)
				return -1;
			if (first_ret < 0)
				first_ret = pos + 2;
			int ptr = ((len & 0x3F) << 8) | buf[pos + 1];
			pos = ptr;
			continue;
		}

		if (pos + 1 + len > buflen)
			return -1;

		if (outlen != 0) {
			if (outlen + 1 >= outsz)
				return -1;
			out[outlen++] = '.';
		}
		if (outlen + len >= outsz)
			return -1;
		for (int i = 0; i < len; i++) {
			unsigned char c = buf[pos + 1 + i];
			out[outlen++] = tolower(c);
		}
		pos += 1 + len;
	}

	out[outlen] = 0;
	return (first_ret >= 0) ? first_ret : pos;
}

static void build_reply(unsigned char *out, int *outlen, uint16_t txid,
                         const char *name, uint32_t ip_addr_n)
{
	unsigned char *p = out;
	uint16_t h;

	h = htons(txid); memcpy(p, &h, 2); p += 2;
	h = htons(0x8400); memcpy(p, &h, 2); p += 2; /* flags: response, authoritative */
	h = 0; memcpy(p, &h, 2); p += 2; /* qdcount */
	h = htons(1); memcpy(p, &h, 2); p += 2; /* ancount */
	h = 0; memcpy(p, &h, 2); p += 2; /* nscount */
	h = 0; memcpy(p, &h, 2); p += 2; /* arcount */

	/* Name as uncompressed labels. */
	const char *s = name;
	while (*s) {
		const char *dot = strchr(s, '.');
		int len = dot ? (int)(dot - s) : (int)strlen(s);
		*p++ = (unsigned char)len;
		memcpy(p, s, len);
		p += len;
		s += len;
		if (*s == '.')
			s++;
	}
	*p++ = 0;

	h = htons(1); memcpy(p, &h, 2); p += 2;        /* type A */
	h = htons(0x8001); memcpy(p, &h, 2); p += 2;   /* class IN, cache-flush bit */
	uint32_t ttl = htonl(240); memcpy(p, &ttl, 4); p += 4;
	h = htons(4); memcpy(p, &h, 2); p += 2;        /* rdlength */
	memcpy(p, &ip_addr_n, 4); p += 4;

	*outlen = (int)(p - out);
}

int main(int argc, char *argv[])
{
	const char *hostname = NULL;
	const char *fixed_addr = NULL;
	int verbose = 0;
	int c;

	while ((c = getopt(argc, argv, "H:a:v")) != -1) {
		switch (c) {
		case 'H': hostname = optarg; break;
		case 'a': fixed_addr = optarg; break;
		case 'v': verbose = 1; break;
		default:
			fprintf(stderr, "Usage: %s -H HOSTNAME [-a ADDRESS] [-v]\n", argv[0]);
			return 1;
		}
	}

	if (!hostname) {
		fprintf(stderr, "Usage: %s -H HOSTNAME [-a ADDRESS] [-v]\n", argv[0]);
		return 1;
	}

	int i;
	for (i = 0; hostname[i] && i < (int)sizeof(g_target) - 6; i++)
		g_target[i] = tolower((unsigned char)hostname[i]);
	strcpy(g_target + i, ".local");

	uint32_t fixed_ip = 0;
	if (fixed_addr) {
		if (inet_pton(AF_INET, fixed_addr, &fixed_ip) != 1) {
			fprintf(stderr, "Invalid -a address\n");
			return 1;
		}
	}

	int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
	if (sock < 0) { perror("socket"); return 1; }

	int one = 1;
	setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
#ifdef SO_REUSEPORT
	setsockopt(sock, SOL_SOCKET, SO_REUSEPORT, &one, sizeof(one));
#endif
	setsockopt(sock, IPPROTO_IP, IP_PKTINFO, &one, sizeof(one));

	struct sockaddr_in bindaddr = {0};
	bindaddr.sin_family = AF_INET;
	bindaddr.sin_addr.s_addr = INADDR_ANY;
	bindaddr.sin_port = htons(MDNS_PORT);
	if (bind(sock, (struct sockaddr *)&bindaddr, sizeof(bindaddr)) < 0) {
		perror("bind");
		return 1;
	}

	struct ip_mreq mreq = {0};
	inet_pton(AF_INET, MDNS_ADDR, &mreq.imr_multiaddr);
	mreq.imr_interface.s_addr = INADDR_ANY;
	if (setsockopt(sock, IPPROTO_IP, IP_ADD_MEMBERSHIP, &mreq, sizeof(mreq)) < 0) {
		perror("IP_ADD_MEMBERSHIP");
		return 1;
	}

	if (fixed_addr)
		printf("cmdnsd: answering for %s -> %s (fixed), listening on %d\n", g_target, fixed_addr, MDNS_PORT);
	else
		printf("cmdnsd: answering for %s -> <auto-detected per interface>, listening on %d\n", g_target, MDNS_PORT);

	unsigned char buf[MAX_PACKET];

	for (;;) {
		struct iovec iov = { .iov_base = buf, .iov_len = sizeof(buf) };
		unsigned char cmbuf[256];
		struct sockaddr_in sender = {0};
		struct msghdr msg = {
			.msg_name = &sender,
			.msg_namelen = sizeof(sender),
			.msg_iov = &iov,
			.msg_iovlen = 1,
			.msg_control = cmbuf,
			.msg_controllen = sizeof(cmbuf),
		};

		ssize_t r = recvmsg(sock, &msg, 0);
		if (r < 0) {
			perror("recvmsg");
			continue;
		}
		if (r < 12)
			continue;

		uint32_t dest_ip_n = 0;
		int have_dest = 0;

		for (struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
			if (cmsg->cmsg_level == IPPROTO_IP && cmsg->cmsg_type == IP_PKTINFO) {
				struct in_pktinfo *pi = (struct in_pktinfo *)CMSG_DATA(cmsg);
				dest_ip_n = pi->ipi_spec_dst.s_addr;
				have_dest = 1;
			}
		}

		uint16_t txid = (buf[0] << 8) | buf[1];
		uint16_t flags = (buf[2] << 8) | buf[3];
		uint16_t qdcount = (buf[4] << 8) | buf[5];

		if (flags & 0x8000)
			continue; /* reply, not query */

		int pos = 12;
		char qname[MAX_NAME];
		char sender_ip[64];
		inet_ntop(AF_INET, &sender.sin_addr, sender_ip, sizeof(sender_ip));

		for (int q = 0; q < qdcount; q++) {
			int next = parse_name(buf, (int)r, pos, qname, sizeof(qname));
			if (next < 0 || next + 4 > r)
				break;

			uint16_t qtype = (buf[next] << 8) | buf[next + 1];
			pos = next + 4;

			if (verbose)
				printf("  query from %s: %s type=%u\n", sender_ip, qname, qtype);

			if (strcmp(qname, g_target) != 0)
				continue;
			if (qtype != 1 && qtype != 255) /* A or ANY */
				continue;

			uint32_t reply_ip = fixed_addr ? fixed_ip : dest_ip_n;
			if (!fixed_addr && !have_dest) {
				if (verbose)
					printf("  could not determine reply address (no IP_PKTINFO), skipping\n");
				continue;
			}

			unsigned char reply[512];
			int replylen;
			build_reply(reply, &replylen, txid, qname, reply_ip);

			int tx = socket(AF_INET, SOCK_DGRAM, 0);
			setsockopt(tx, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
			struct sockaddr_in dst = {0};
			dst.sin_family = AF_INET;
			dst.sin_port = htons(MDNS_PORT);
			inet_pton(AF_INET, MDNS_ADDR, &dst.sin_addr);
			sendto(tx, reply, replylen, 0, (struct sockaddr *)&dst, sizeof(dst));
			close(tx);

			char reply_ip_str[64];
			struct in_addr ia = { .s_addr = reply_ip };
			inet_ntop(AF_INET, &ia, reply_ip_str, sizeof(reply_ip_str));
			printf("  answered %s -> %s (query from %s)\n", qname, reply_ip_str, sender_ip);
		}
	}

	return 0;
}
