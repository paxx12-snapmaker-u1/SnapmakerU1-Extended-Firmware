// SPDX-License-Identifier: GPL-3.0-or-later
// SPDX-PackageHomePage: https://github.com/paxx12-snapmaker-u1/SnapmakerU1-Extended-Firmware
// SPDX-FileCopyrightText: Copyright (c) 2026 @paxx12

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <sys/wait.h>
#include <sys/types.h>
#include <errno.h>
#include <syslog.h>
#include <time.h>
#include <stdbool.h>
#include <fcntl.h>
#include <stdarg.h>
#include <getopt.h>
#include <libgen.h>

static volatile sig_atomic_t received_signal = 0;
static volatile pid_t child_pid = 0;
static bool use_syslog = false;

static void log_get_timestamp(char *buf, size_t size)
{
    time_t now = time(NULL);
    struct tm *tm_info = localtime(&now);
    strftime(buf, size, "[%H:%M:%S]", tm_info);
}

static void log_info(const char *format, ...)
{
    char timestamp[16];
    log_get_timestamp(timestamp, sizeof(timestamp));
    fprintf(stdout, "%s ", timestamp);
    va_list args;
    va_start(args, format);
    vfprintf(stdout, format, args);
    va_end(args);
    if (use_syslog) {
        va_start(args, format);
        vsyslog(LOG_INFO, format, args);
        va_end(args);
    }
    fflush(stdout);
}

static void log_error(const char *format, ...)
{
    char timestamp[16];
    log_get_timestamp(timestamp, sizeof(timestamp));
    fprintf(stderr, "%s ", timestamp);
    va_list args;
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    if (use_syslog) {
        va_start(args, format);
        vsyslog(LOG_ERR, format, args);
        va_end(args);
    }
    fflush(stderr);
}

static void signal_handler(int sig)
{
    received_signal = sig;
    if (child_pid > 0) {
        kill(child_pid, sig);
    }
}

static const char *strip_timestamp(const char *line)
{
    int h, m, s;
    int n;

    if (sscanf(line, "[%d:%d:%d]%n", &h, &m, &s, &n) != 3) {
        return line;
    }
    if (h < 0 || h > 23 || m < 0 || m > 59 || s < 0 || s > 59) {
        return line;
    }

    const char *after = line + n;
    while (*after == ' ' || *after == '\t') {
        after++;
    }
    return after;
}

static void log_line(const char *line, const char *suffix)
{
    const char *after = strip_timestamp(line);

    if (after == line) {
        char timestamp[16];
        log_get_timestamp(timestamp, sizeof(timestamp));
        fprintf(stderr, "%s %s%s", timestamp, line, suffix);
    } else {
        fprintf(stderr, "%s%s", line, suffix);
    }

    if (use_syslog) {
        syslog(LOG_INFO, "%s", after);
    }
}

static void log_child_output(int fd)
{
    char buf[4096];
    ssize_t n;

    while ((n = read(fd, buf, sizeof(buf) - 1)) > 0) {
        buf[n] = '\0';
        char *line = buf;
        char *newline;

        while ((newline = strchr(line, '\n')) != NULL) {
            *newline = '\0';
            log_line(line, "\n");
            line = newline + 1;
        }

        if (*line != '\0') {
            log_line(line, "");
        }
    }
}

static void print_usage(const char *prog)
{
    printf("Usage: %s [options] <command> [args...]\n", prog);
    printf("\n");
    printf("Options:\n");
    printf("  --retry <seconds>   Retry delay in seconds (default: 3)\n");
    printf("  --syslog            Enable syslog logging\n");
    printf("  --help              Show this help\n");
    printf("\n");
    printf("Examples:\n");
    printf("  %s sleep 5\n", prog);
    printf("  %s --retry 5 --syslog /path/to/program arg1 arg2\n", prog);
}

int main(int argc, char *argv[])
{
    int retry_seconds = 3;
    int retry_count = 0;
    int status;
    int exit_code = 0;
    char **cmd_argv = NULL;
    int cmd_argc = 0;
    struct sigaction sa;
    int opt;

    enum {
        OPT_RETRY = 1,
        OPT_SYSLOG,
        OPT_HELP,
    };

    static struct option long_options[] = {
        {"retry", required_argument, 0, OPT_RETRY},
        {"syslog", no_argument, 0, OPT_SYSLOG},
        {"help", no_argument, 0, OPT_HELP},
        {0, 0, 0, 0}
    };

    while ((opt = getopt_long(argc, argv, "+", long_options, NULL)) != -1) {
        switch (opt) {
        case OPT_RETRY:
            retry_seconds = atoi(optarg);
            break;
        case OPT_SYSLOG:
            use_syslog = true;
            break;
        case OPT_HELP:
            print_usage(argv[0]);
            return 0;
        default:
            print_usage(argv[0]);
            return 1;
        }
    }

    cmd_argc = argc - optind;
    cmd_argv = &argv[optind];

    if (cmd_argc == 0) {
        fprintf(stderr, "Error: No command specified\n");
        print_usage(argv[0]);
        return 1;
    }

    if (use_syslog) {
        char *app_name = basename(cmd_argv[0]);
        openlog(strdup(app_name), LOG_PID, LOG_USER);
        setlogmask(LOG_UPTO(LOG_DEBUG));
    }

    log_info("fake-service - built %s (%s)\n", __DATE__, __FILE__);

    fprintf(stdout, "Command:");
    for (int i = 0; i < cmd_argc; i++) {
        fprintf(stdout, " %s", cmd_argv[i]);
    }
    fprintf(stdout, "Retry delay: %d seconds\n", retry_seconds);

    sa.sa_handler = signal_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = 0;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    sigaction(SIGQUIT, &sa, NULL);

    while (received_signal == 0) {
        int pipe_fd[2];

        if (pipe(pipe_fd) < 0) {
            log_error("pipe: %s\n", strerror(errno));
            exit(1);
        }

        child_pid = fork();

        if (child_pid < 0) {
            log_error("fork: %s\n", strerror(errno));
            exit(1);
        }

        if (child_pid == 0) {
            close(pipe_fd[0]);
            dup2(pipe_fd[1], STDOUT_FILENO);
            dup2(pipe_fd[1], STDERR_FILENO);
            close(pipe_fd[1]);

            signal(SIGTERM, SIG_DFL);
            signal(SIGINT, SIG_DFL);
            signal(SIGHUP, SIG_DFL);
            signal(SIGQUIT, SIG_DFL);

            execvp(cmd_argv[0], cmd_argv);
            fprintf(stderr, "execvp: %s: %s\n", cmd_argv[0], strerror(errno));
            exit(127);
        }

        close(pipe_fd[1]);

        log_error("Starting child process %d\n", child_pid);

        log_child_output(pipe_fd[0]);
        close(pipe_fd[0]);

        while (1) {
            pid_t result = waitpid(child_pid, &status, 0);

            if (result == -1) {
                if (errno == EINTR) {
                    continue;
                }
                log_error("waitpid: %s\n", strerror(errno));
                exit(1);
            }
            break;
        }

        child_pid = 0;

        if (WIFEXITED(status)) {
            exit_code = WEXITSTATUS(status);
            if (exit_code == 0) {
                log_error("Child exited normally with code 0, exiting\n");
                break;
            }
            log_error("Child exited with code %d\n", exit_code);
        } else if (WIFSIGNALED(status)) {
            int sig = WTERMSIG(status);
            log_error("Child terminated by signal %d\n", sig);
            exit_code = 128 + sig;
        }

        if (received_signal != 0) {
            log_error("Monitor received signal %d, exiting\n", received_signal);
            break;
        }

        log_error("Restarting child process in %d seconds (retry %d)\n",
                retry_seconds, ++retry_count);
        sleep(retry_seconds);
    }

    if (use_syslog) {
        closelog();
    }
    return exit_code;
}
